import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VOICE = {'name': 'Example', 'language': 'de', 'gender': 'female',
         'voice_id': '12345678-1234-1234-1234-123456789abc'}


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / '.hermes'
        self.home.mkdir()
        native = patch('hermes_constants._get_platform_default_hermes_home', return_value=self.home)
        native.start()
        self.addCleanup(native.stop)
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_shared_store_pads_five_slots_without_profile_writes(self):
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        self.assertEqual(store.read_favorites(), [dict.fromkeys(VOICE, '') for _ in range(5)])
        saved = store.write_favorites([VOICE])
        self.assertEqual(len(saved), 5)
        self.assertEqual(saved[0], VOICE)
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        token = set_hermes_home_override(self.home / 'profiles' / 'work')
        try:
            self.assertEqual(store.read_favorites(), saved)
        finally:
            reset_hermes_home_override(token)
        target = self.home / 'plugin-data/cartesia-voice/favorites.json'
        self.assertTrue(target.is_file())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.home / 'profiles').exists())


    def test_validation_and_unsafe_state_fail_closed(self):
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        for slots in ([VOICE] * 6, [VOICE, dict(VOICE, voice_id=VOICE['voice_id'].upper())],
                      [dict(VOICE, name='')], [dict(VOICE, voice_id='bad')],
                      [dict(VOICE, gender='other')], [dict(VOICE, extra='x')],
                      [dict(VOICE, language='de' + '-ab' * 6000)]):
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                store.write_favorites(slots)
        store.write_favorites([VOICE])
        target = self.home / 'plugin-data/cartesia-voice/favorites.json'
        target.write_bytes(b'invalid json')
        for action in (store.read_favorites, lambda: store.write_favorites([])):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(target.read_bytes(), b'invalid json')
        target.unlink()
        outside = self.home / 'untouched'
        outside.write_text('unchanged')
        target.symlink_to(outside)
        for action in (store.read_favorites, lambda: store.write_favorites([])):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(outside.read_text(), 'unchanged')
        target.unlink()
        lock = target.with_name('favorites.lock')
        lock.unlink()
        lock.symlink_to(outside)
        with self.assertRaises(ValueError):
            store.write_favorites([])
        lock.unlink()
        directory = target.parent
        directory.rmdir()
        directory.symlink_to(self.home)
        with self.assertRaises(ValueError):
            store.write_favorites([])

    def test_atomic_failure_preserves_valid_state(self):
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        original = store.write_favorites([VOICE])
        with patch.object(store.os, 'replace', side_effect=OSError('failure')):
            with self.assertRaises(ValueError):
                store.write_favorites([])
        self.assertEqual(store.read_favorites(), original)
        self.assertEqual(sorted(p.name for p in (self.home / 'plugin-data/cartesia-voice').iterdir()), ['favorites.json', 'favorites.lock'])

    def test_provider_catalog_is_shared_local_metadata_and_keeps_configured_voice(self):
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        store.write_favorites([VOICE])
        plugin = load_file('cartesia_test_plugin', ROOT / '__init__.py')
        with patch('hermes_cli.config.load_config', return_value={'tts': {'cartesia': {'voice': 'existing'}}}), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            voices = plugin.CartesiaProvider().list_voices()
            self.assertEqual(voices[0]['id'], VOICE['voice_id'])
            self.assertEqual(voices[0]['language'], 'de')
            self.assertEqual(voices[0]['gender'], 'female')
            self.assertEqual(voices[0]['display'], 'Example')
            self.assertEqual(voices[1]['id'], 'existing')

    def test_configured_voice_survives_corrupt_favorites(self):
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        store.write_favorites([VOICE])
        (self.home / 'plugin-data/cartesia-voice/favorites.json').write_bytes(b'invalid json')
        plugin = load_file('cartesia_test_plugin', ROOT / '__init__.py')
        with patch.object(plugin.CartesiaProvider, 'default_voice', return_value='existing'):
            self.assertEqual(plugin.CartesiaProvider().list_voices(),
                             [{'id': 'existing', 'display': 'Configured Cartesia voice'}])

    def test_unsupported_store_and_provider_fallback(self):
        import builtins
        store = load_file('cartesia_test_favorites', ROOT / 'favorites.py')
        plugin = load_file('cartesia_test_plugin', ROOT / '__init__.py')
        native_import = builtins.__import__

        def no_fcntl(name, *args, **kwargs):
            if name == 'fcntl':
                raise AssertionError('fcntl imported before platform guard')
            return native_import(name, *args, **kwargs)

        for capability in ('platform', 'directory'):
            guard = (patch.object(sys, 'platform', 'win32') if capability == 'platform'
                     else patch.dict(os.__dict__))
            with self.subTest(capability=capability), guard, patch('builtins.__import__', side_effect=no_fcntl):
                if capability == 'directory':
                    del os.O_DIRECTORY
                for action in (store.read_favorites, lambda: store.write_favorites([])):
                    with self.assertRaisesRegex(ValueError, 'requires Linux/macOS'):
                        action()
                with patch.object(plugin.CartesiaProvider, 'default_voice', return_value='existing'):
                    self.assertEqual(plugin.CartesiaProvider().list_voices(),
                                     [{'id': 'existing', 'display': 'Configured Cartesia voice'}])

    def test_api_unsupported_favorites_returns_422(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        api = load_file('hermes_dashboard_plugin_cartesia-voice', ROOT / 'dashboard/plugin_api.py')
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/cartesia-voice')
        with TestClient(app) as client, patch.object(sys, 'platform', 'win32'):
            path = '/api/plugins/cartesia-voice/favorites'
            for response in (client.get(path), client.put(path, json={
                    'favorites': [], 'expected_profile': 'default'})):
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn('requires Linux/macOS', response.json()['detail'])

    def test_api_profile_guard_and_selection_preserve_config(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from hermes_cli.config import load_config, save_config
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        api = load_file('hermes_dashboard_plugin_cartesia-voice', ROOT / 'dashboard/plugin_api.py')
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/cartesia-voice')
        with TestClient(app) as client, patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            path = '/api/plugins/cartesia-voice'
            save_config({'tts': {'provider': 'edge', 'voice': 'old', 'cartesia': {'model': 'keep'}}, 'voice': {'auto_tts': False}, 'custom': {'keep': 42}})
            state = client.get(path + '/favorites').json()
            self.assertEqual(state['profile'], 'default')
            self.assertEqual(state['catalog_scope'], 'shared-local-user')
            self.assertEqual(state['selection']['voice_id'], 'old')
            self.assertEqual(client.put(path + '/favorites', json={'favorites': [VOICE], 'expected_profile': 'other'}).status_code, 409)
            self.assertEqual(client.put(path + '/favorites', json={'favorites': [VOICE], 'expected_profile': 'default'}).status_code, 200)
            self.assertEqual(load_config()['tts']['voice'], 'old')
            self.assertEqual(client.post(path + '/selection', json={'voice_id': 'unknown', 'expected_profile': 'default'}).status_code, 422)
            response = client.post(path + '/selection', json={'voice_id': VOICE['voice_id'], 'expected_profile': 'default'})
            self.assertEqual(response.status_code, 200, response.text)
            config = load_config()
            self.assertEqual(config['tts']['cartesia'], {'model': 'keep', 'voice': VOICE['voice_id'], 'language': 'de'})
            self.assertEqual(config['tts']['provider'], 'edge')
            self.assertFalse(config['voice']['auto_tts'])
            self.assertEqual(config['custom']['keep'], 42)
            self.assertEqual(client.put(path + '/favorites', json={'favorites': [], 'expected_profile': 'default'}).status_code, 200)
            self.assertEqual(load_config()['tts']['cartesia']['voice'], VOICE['voice_id'])
            named = self.home / 'profiles' / 'work'
            named.mkdir(parents=True)
            token = set_hermes_home_override(named)
            try:
                self.assertEqual(client.get(path + '/favorites').json()['profile'], 'work')
                self.assertEqual(client.post(path + '/selection', json={'voice_id': VOICE['voice_id'], 'expected_profile': 'default'}).status_code, 409)
                self.assertFalse((named / 'config.yaml').exists())
                self.assertEqual(client.put(path + '/favorites', json={'favorites': [dict(VOICE, language='en')], 'expected_profile': 'work'}).status_code, 200)
                self.assertEqual(client.post(path + '/selection', json={'voice_id': VOICE['voice_id'], 'expected_profile': 'work'}).status_code, 200)
                self.assertEqual(load_config()['tts']['cartesia']['language'], 'en')
            finally:
                reset_hermes_home_override(token)
            self.assertEqual(load_config()['tts']['cartesia']['language'], 'de')
            self.assertEqual(client.get(path + '/favorites').json()['favorites'][0]['language'], 'en')


if __name__ == '__main__':
    unittest.main()
