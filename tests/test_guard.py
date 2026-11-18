import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src'
spec = importlib.util.spec_from_file_location('guard', SOURCE/'guard.py')
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)

class PolicyTests(unittest.TestCase):
    def test_locked_has_no_core_or_public_tunnel_pass(self):
        rules=G.render()
        self.assertNotIn('pfg-core',rules)
        self.assertNotIn('pfg-tun',rules)
        self.assertTrue(rules.endswith('block drop out quick all label "pfg-deny"\n'))

    def test_connected_restricts_core_to_dedicated_group(self):
        rules=G.render(412,(123,'utun4',(('inet','198.18.0.1'),)))
        self.assertIn('user 0 group 412',rules)
        self.assertIn('on utun4 inet from 198.18.0.1',rules)
        self.assertLess(rules.index('pfg-fragment'),rules.index('pfg-core'))
        self.assertIn('pfg-lan4',rules)

    def test_rejects_shared_group_and_injected_interface(self):
        for gid,nic in [(20,'utun4'),(80,'utun4'),(0,'utun4'),(412,'utun4\npass out all')]:
            with self.assertRaises(G.GuardError):
                G.render(gid,(123,nic,(('inet','198.18.0.1'),)))

    def test_tailscale_tcp_requires_exact_held_socket(self):
        rules=G.socket_rules('f12\nPTCP\nn192.168.1.2:51234->203.0.113.3:443\n',0)
        self.assertEqual(len(rules),1)
        rule=next(iter(rules))
        self.assertIn('from 192.168.1.2 port 51234 to 203.0.113.3 port 443 user 0',rule)
        self.assertNotIn('keep state',rule)

    def test_tailscale_tcp_listener_is_not_a_bypass(self):
        self.assertEqual(G.socket_rules('f12\nPTCP\nn*:443\n',0),set())

    def test_tailscale_udp_matches_only_observed_source_port(self):
        rules=G.socket_rules('f12\nPUDP\nn*:41641\n',0)
        self.assertEqual(len(rules),2)
        for rule in rules:
            self.assertIn('from any port 41641 to any user 0',rule)

    def test_ipv6_and_loopback(self):
        rules=G.socket_rules('f1\nPTCP\nn[2001:db8::1]:51234->[2001:db8::2]:443\nf2\nPTCP\nn127.0.0.1:10079->127.0.0.1:51235\n',0)
        self.assertEqual(len(rules),1)
        self.assertIn('inet6',next(iter(rules)))

    def test_untrusted_tailscale_paths_are_rejected(self):
        self.assertFalse(G.trusted_tailscale_path('/tmp/Tailscale'))
        self.assertFalse(G.trusted_tailscale_path('/Applications/Tailscale-copy.app/Contents/MacOS/Tailscale'))
        self.assertTrue(G.trusted_tailscale_path('/Applications/Tailscale.app/Contents/MacOS/Tailscale'))

    def test_tailscale_pass_is_present_even_while_locked(self):
        ts=G.socket_rules('f12\nPUDP\nn*:41641\n',0)
        rules=G.render(ts_rules=sorted(ts))
        self.assertIn('pfg-ts-socket',rules)
        self.assertNotIn('pfg-core',rules)
        self.assertNotIn('to any port 443',rules)

class ReconcileTests(unittest.TestCase):
    def controller(self):
        with patch.object(G,'read_json',side_effect=lambda name,fallback=None:fallback),patch.object(G.U,'boot_id',return_value='test'):
            c=G.Controller()
        c.settings={'enabled':True}
        c.install_rules=lambda rules,force=False:setattr(c,'generated',rules)
        return c

    def test_core_disappearance_removes_public_exception(self):
        c=self.controller()
        c.stable_path=(1,'utun4',(('inet','198.18.0.1'),))
        c.stable_since=-100
        with patch.object(G.U,'processes',return_value=[]),patch.object(G,'tailscale_rules',return_value=([],0)),patch.object(G.U,'group_id',return_value=412),patch.object(G.U,'owned_path',side_effect=G.U.GuardError('absent')):
            c.tick()
        self.assertEqual(c.state['mode'],'locked')
        self.assertNotIn('pfg-core',c.generated)

    def test_healthy_path_retains_smart_routing(self):
        c=self.controller();path=(1,'utun4',(('inet','198.18.0.1'),))
        c.stable_path=path;c.stable_since=-100
        with patch.object(G.U,'processes',return_value=[]),patch.object(G,'tailscale_rules',return_value=([],0)),patch.object(G.U,'group_id',return_value=412),patch.object(G.U,'owned_path',return_value=path): c.tick()
        self.assertEqual(c.state['mode'],'connected')
        self.assertIn('user 0 group 412',c.generated)
        self.assertIn('to any',c.generated)

    def test_new_tunnel_is_locked_until_stable(self):
        c=self.controller();path=(1,'utun5',(('inet','198.18.0.1'),))
        with patch.object(G.U,'processes',return_value=[]),patch.object(G,'tailscale_rules',return_value=([],0)),patch.object(G.U,'group_id',return_value=412),patch.object(G.U,'owned_path',return_value=path): c.tick()
        self.assertEqual(c.state['mode'],'locked')
        self.assertNotIn('pfg-tun',c.generated)

    def test_explicit_test_lock_is_not_immediately_reopened(self):
        c=self.controller();path=(1,'utun4',(('inet','198.18.0.1'),))
        c.stable_path=path;c.stable_since=-100;c.manual_lock_until=float('inf')
        with patch.object(G.U,'processes',return_value=[]),patch.object(G,'tailscale_rules',return_value=([],0)),patch.object(G.U,'group_id',return_value=412),patch.object(G.U,'owned_path',return_value=path): c.tick()
        self.assertEqual(c.state['mode'],'locked')
        self.assertNotIn('pfg-core',c.generated)

    def test_off_state_never_reinstalls_rules(self):
        c=self.controller();c.settings['enabled']=False
        c.tick()
        self.assertFalse(hasattr(c,'generated'))

    def test_enable_preflights_identity_before_any_network_mutation(self):
        c=self.controller();c.settings={'enabled':False};c.pf_record={}
        c.check_layout=lambda:None
        with patch.object(G.U,'read_rules',return_value=''),patch.object(G.U,'group_id',return_value=412),patch.object(G.U,'owned_path',side_effect=G.U.GuardError('wrong identity')):
            with self.assertRaises(G.U.GuardError):c.enable()
        self.assertFalse(c.settings['enabled'])
        self.assertFalse(hasattr(c,'generated'))

if __name__=='__main__': unittest.main()
