import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location('cartesia_plugin', ROOT / '__init__.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        native = patch('hermes_constants._get_platform_default_hermes_home', return_value=root)
        native.start()
        self.addCleanup(native.stop)
        home = patch.dict('os.environ', {'HERMES_HOME': str(root)})
        home.start()
        self.addCleanup(home.stop)

    def test_availability_and_metadata_follow_live_profile(self):
        from agent.secret_scope import (set_secret_scope, reset_secret_scope,
                                        set_multiplex_context, reset_multiplex_context)
        module = load_plugin()
        provider = module.CartesiaProvider()
        mode = set_multiplex_context(True)
        try:
            with patch('hermes_cli.config.load_config', return_value={'tts': {'cartesia': {'voice': 'configured-voice'}}}), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
                for mapping, available in [({'CARTESIA_API_KEY': 'test-only-key'}, True), ({}, False)]:
                    token = set_secret_scope(mapping)
                    try:
                        self.assertEqual(provider.is_available(), available)
                    finally:
                        reset_secret_scope(token)
                token = set_secret_scope(None)
                try:
                    self.assertFalse(provider.is_available())
                finally:
                    reset_secret_scope(token)
                self.assertEqual(provider.default_voice(), 'configured-voice')
                self.assertEqual(provider.list_voices(), [{'id': 'configured-voice', 'display': 'Configured Cartesia voice'}])
                self.assertEqual(provider.default_model(), 'sonic-3.5')
                self.assertEqual(provider.list_models()[0]['id'], 'sonic-3.5')
                self.assertEqual(provider.get_setup_schema()['env_vars'][0]['key'], 'CARTESIA_API_KEY')
            with patch('hermes_cli.config.load_config', return_value={}):
                self.assertEqual(provider.list_voices(), [])
                self.assertIsNone(provider.default_voice())
        finally:
            reset_multiplex_context(mode)

    def test_wav_synthesis_request_and_atomic_result(self):
        from agent.secret_scope import set_secret_scope, reset_secret_scope
        module = load_plugin()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes(b'\x00\x00' * 100)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.headers = {'Content-Type': 'audio/wav'}
        response.read.return_value = audio.getvalue()
        opener = Mock()
        opener.open.return_value = response
        token = set_secret_scope({'CARTESIA_API_KEY': 'fake-key-for-tests'})
        try:
            with tempfile.TemporaryDirectory() as directory, patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'global-voice', 'model': 'global-model', 'speed': 1.1}}), patch('urllib.request.build_opener', return_value=opener):
                target = Path(directory) / 'speech.wav'
                result = module.CartesiaProvider().synthesize('Hallo Welt', str(target), format='wav', ignored_future_option=True)
                self.assertEqual(result, str(target))
                self.assertEqual(target.read_bytes(), audio.getvalue())
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, 'https://api.cartesia.ai/tts/bytes')
                self.assertEqual(request.method, 'POST')
                self.assertEqual(request.get_header('Authorization'), 'Bearer fake-key-for-tests')
                self.assertEqual(request.get_header('Cartesia-version'), '2026-08-14')
                self.assertEqual(opener.open.call_args.kwargs['timeout'], 60)
                self.assertEqual(json.loads(request.data), {'model_id': 'global-model', 'transcript': 'Hallo Welt', 'voice': 'global-voice', 'generation_config': {'speed': 1.1}, 'output_format': {'container': 'wav', 'encoding': 'pcm_s16le', 'sample_rate': 44100}})
        finally:
            reset_secret_scope(token)

    def test_streaming_wav_sentinels_are_normalized_without_changing_pcm(self):
        module = load_plugin()
        pcm = b'\x01\x00\xff\xff' * 56448  # 225792 actual PCM bytes.
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes(pcm)
        for extra in (b'', b'JUNK\x03\x00\x00\x00abc\x00'):
            with self.subTest(extra_chunk=bool(extra)), tempfile.TemporaryDirectory() as directory:
                streaming = bytearray(audio.getvalue())
                streaming[36:36] = extra
                data_offset = 36 + len(extra)
                streaming[4:8] = b'\xff' * 4
                streaming[data_offset + 4:data_offset + 8] = b'\xff' * 4
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.status = 200
                response.headers = {'Content-Type': 'audio/wav'}
                response.read.return_value = bytes(streaming)
                opener = Mock()
                opener.open.return_value = response
                target = Path(directory) / 'speech.wav'
                with patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'test-voice'}}), patch('agent.secret_scope.get_secret', return_value='fake-test-key'), patch('urllib.request.build_opener', return_value=opener):
                    result = module.CartesiaProvider().synthesize('hello', str(target), format='wav')
                saved = Path(result).read_bytes()
                expected = bytearray(streaming)
                expected[4:8] = (len(expected) - 8).to_bytes(4, 'little')
                expected[data_offset + 4:data_offset + 8] = len(pcm).to_bytes(4, 'little')
                self.assertEqual(saved, bytes(expected))
                with wave.open(io.BytesIO(saved), 'rb') as reader:
                    self.assertEqual(reader.getnframes(), len(pcm) // 2)
                    self.assertEqual(reader.readframes(reader.getnframes()), pcm)

    def test_formats_and_provider_settings(self):
        module = load_plugin()
        for requested, container in [('mp3', 'mp3'), ('ogg', 'wav'), ('opus', 'wav'), ('flac', 'wav')]:
            with self.subTest(format=requested), tempfile.TemporaryDirectory() as directory:
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.status = 200
                response.headers = {'Content-Type': 'audio/mpeg' if container == 'mp3' else 'audio/wav'}
                audio = io.BytesIO()
                with wave.open(audio, 'wb') as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(24000)
                    writer.writeframes(b'\x00\x00' * 100)
                response.read.return_value = b'\xff\xfb\x90\x00' + b'\x00' * 413 if container == 'mp3' else audio.getvalue()
                opener = Mock()
                opener.open.return_value = response
                cfg = {'tts': {'voice': 'global', 'cartesia': {'voice': 'profile-voice', 'model': 'sonic-3.5', 'speed': 1.2, 'language': 'de', 'timeout': 30, 'sample_rate': 24000, 'api_version': '2026-08-14'}}}
                with patch('hermes_cli.config.load_config', return_value=cfg), patch('agent.secret_scope.get_secret', return_value='fake-test-key'), patch('urllib.request.build_opener', return_value=opener):
                    result = module.CartesiaProvider().synthesize('Guten Tag', str(Path(directory) / f'speech.{requested}'), voice='global', model='global', speed=1.0, format=requested)
                self.assertEqual(Path(result).suffix, '.' + container)
                self.assertEqual(Path(result).read_bytes(), response.read.return_value)
                request = opener.open.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(payload['voice'], 'profile-voice')
                self.assertEqual(payload['model_id'], 'sonic-3.5')
                self.assertEqual(payload['language'], 'de')
                self.assertEqual(payload['generation_config']['speed'], 1.2)
                expected = {'container': container, 'sample_rate': 24000}
                expected.update({'bit_rate': 128000} if container == 'mp3' else {'encoding': 'pcm_s16le'})
                self.assertEqual(payload['output_format'], expected)
                self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)

    def test_invalid_configuration_fails_before_network(self):
        module = load_plugin()
        cases = [
            ({'voice': ''}, 'test-key', 'hello', 'voice'),
            ({'voice': 'voice'}, None, 'hello', 'API key'),
            ({'voice': 'voice', 'timeout': 0}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'timeout': float('inf')}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'timeout': 301}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'sample_rate': 12345}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'speed': 4}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'speed': True}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice', 'api_version': 'bad\r\nversion'}, 'test-key', 'hello', 'configuration'),
            ({'voice': 'voice'}, 'test-key', '', 'text'),
            ({'voice': 'voice'}, 'test-key', 'x' * 20001, 'text'),
            ({'voice': 'v' * 300000}, 'test-key', 'hello', 'request body'),
        ]
        for cfg, key, text, message in cases:
            with self.subTest(message=message), patch('hermes_cli.config.load_config', return_value={'tts': {'cartesia': cfg}}), patch('agent.secret_scope.get_secret', return_value=key), patch('urllib.request.OpenerDirector.open') as network:
                with self.assertRaisesRegex(RuntimeError, message):
                    module.CartesiaProvider().synthesize(text, str(ROOT / '.scratch' / 'must-not-exist.wav'), format='wav')
                network.assert_not_called()
        with patch('agent.secret_scope.get_secret', side_effect=RuntimeError('secret upstream detail')):
            with self.assertRaisesRegex(RuntimeError, '^Cartesia API key unavailable') as error:
                module.CartesiaProvider().synthesize('hello', 'unused.wav')
            self.assertIsNone(error.exception.__cause__)

    def test_http_and_transport_errors_are_sanitized_without_files(self):
        from urllib.error import HTTPError, URLError
        module = load_plugin()
        errors = [HTTPError('https://api.cartesia.ai/tts/bytes', code, 'private text and key', {}, io.BytesIO(b'private transcript')) for code in (401, 429, 500)] + [URLError('private key'), TimeoutError('private transcript')]
        for upstream in errors:
            with self.subTest(error=type(upstream).__name__), tempfile.TemporaryDirectory() as directory, patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'test-voice'}}), patch('agent.secret_scope.get_secret', return_value='fake-test-key'), patch('urllib.request.OpenerDirector.open', side_effect=upstream):
                target = Path(directory) / 'speech.wav'
                target.write_bytes(b'prior output')
                with self.assertRaisesRegex(RuntimeError, '^Cartesia request failed') as error:
                    module.CartesiaProvider().synthesize('private transcript', str(target), format='wav')
                self.assertNotIn('private', str(error.exception))
                self.assertIsNone(error.exception.__cause__)
                self.assertEqual(target.read_bytes(), b'prior output')
                self.assertEqual(list(Path(directory).iterdir()), [target])
                if isinstance(upstream, HTTPError):
                    self.assertTrue(upstream.fp.closed, 'HTTP error response must be closed')

    def test_response_validation_and_limits_preserve_prior_output(self):
        module = load_plugin()
        cases = [
            (200, 'audio/wav', b'', {}),
            (200, 'application/json', b'{"private": "error"}', {}),
            (200, 'audio/wav', b'RIFF' + b'\x00' * 100, {}),
            (200, 'audio/mpeg', b'ID3' + b'\x00' * 30, {}),
            (200, 'audio/mpeg', b'\xff\xfb\x90\x00', {}),
            (204, 'audio/wav', b'not audio', {}),
            (200, 'audio/wav', b'not audio', {'Content-Length': str(32 * 1024 * 1024 + 1)}),
            (200, 'audio/wav', b'x' * (32 * 1024 * 1024 + 1), {}),
        ]
        for status, mime, data, headers in cases:
            with self.subTest(status=status, mime=mime, size=len(data)), tempfile.TemporaryDirectory() as directory, patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'test-voice'}}), patch('agent.secret_scope.get_secret', return_value='fake-test-key'):
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.status = status
                response.headers = dict(headers, **{'Content-Type': mime})
                response.read.return_value = data
                opener = Mock()
                opener.open.return_value = response
                target = Path(directory) / ('speech.mp3' if mime == 'audio/mpeg' else 'speech.wav')
                target.write_bytes(b'prior output')
                with patch('urllib.request.build_opener', return_value=opener), self.assertRaisesRegex(RuntimeError, 'Cartesia'):
                    module.CartesiaProvider().synthesize('hello', str(target), format=target.suffix[1:])
                self.assertEqual(target.read_bytes(), b'prior output')
                self.assertEqual(list(Path(directory).iterdir()), [target])
                if response.read.called:
                    self.assertEqual(response.read.call_args.args, (32 * 1024 * 1024 + 1,))

    def test_cross_origin_redirects_never_forward_credentials(self):
        from email.message import Message
        from urllib.response import addinfourl
        module = load_plugin()
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'test-voice'}}), patch('agent.secret_scope.get_secret', return_value='fake-test-key'):
                def redirect(request):
                    headers = Message()
                    headers['Location'] = 'https://other.example/collect'
                    result = addinfourl(io.BytesIO(b''), headers, request.full_url, status)
                    result.msg = 'Redirect'
                    return result
                with patch('urllib.request.HTTPSHandler.https_open', side_effect=redirect) as transport:
                    with self.assertRaisesRegex(RuntimeError, 'Cartesia request failed'):
                        module.CartesiaProvider().synthesize('hello', str(ROOT / '.scratch' / 'unused.wav'), format='wav')
                    self.assertEqual(transport.call_count, 1, 'Redirect must not trigger a second request')

    def test_output_replace_failure_is_atomic_and_cleans_temporary_file(self):
        module = load_plugin()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes(b'\x00\x00' * 100)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.headers = {'Content-Type': 'audio/wav'}
        response.read.return_value = audio.getvalue()
        opener = Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch('hermes_cli.config.load_config', return_value={'tts': {'voice': 'test-voice'}}), patch('agent.secret_scope.get_secret', return_value='fake-test-key'), patch('urllib.request.build_opener', return_value=opener), patch('os.replace', side_effect=OSError('private path')):
            target = Path(directory) / 'speech.wav'
            target.write_bytes(b'prior output')
            with self.assertRaisesRegex(RuntimeError, '^Cartesia output could not be written'):
                module.CartesiaProvider().synthesize('hello', str(target), format='wav')
            self.assertEqual(target.read_bytes(), b'prior output')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_registration_real_abc_without_network(self):
        self.assertTrue((ROOT / '__init__.py').is_file(), 'Plugin entrypoint missing')
        from agent.tts_provider import TTSProvider
        module = load_plugin()
        ctx = Mock()
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            module.register(ctx)
        provider = ctx.register_tts_provider.call_args.args[0]
        self.assertIsInstance(provider, TTSProvider)
        self.assertEqual(provider.name, 'cartesia')
        self.assertTrue(provider.voice_compatible)
        self.assertFalse(provider.streams_pcm)


if __name__ == '__main__':
    unittest.main()
