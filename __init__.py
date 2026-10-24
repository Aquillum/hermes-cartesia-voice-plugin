"""Native, whole-file Cartesia TTS provider for Hermes."""
import io
import json
import wave
import math
import os
import tempfile
import re
from pathlib import Path
from urllib.error import HTTPError
import urllib.request

from agent.tts_provider import TTSProvider


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise RuntimeError('Cartesia redirects are refused.')


class CartesiaProvider(TTSProvider):
    name = 'cartesia'
    voice_compatible = True

    def _settings(self):
        from hermes_cli.config import load_config
        tts = load_config().get('tts', {})
        return tts, tts.get('cartesia', {})

    def is_available(self):
        from agent.secret_scope import get_secret
        try:
            return bool(get_secret('CARTESIA_API_KEY') and self.default_voice())
        except Exception:
            return False

    def list_voices(self):
        from .favorites import read_favorites
        try:
            favorites = read_favorites()
        except ValueError:
            import logging
            logging.getLogger(__name__).warning('Cartesia favorites unavailable; using configured voice metadata.')
            favorites = []
        voices = [{'id': slot['voice_id'], 'display': slot['name'],
                   'language': slot['language'], 'gender': slot['gender']}
                  for slot in favorites if slot['voice_id']]
        voice = self.default_voice()
        if voice and not any(item['id'].lower() == voice.lower() for item in voices):
            voices.append({'id': voice, 'display': 'Configured Cartesia voice'})
        return voices

    def default_voice(self):
        tts, cfg = self._settings()
        return cfg.get('voice') or tts.get('voice') or None

    def default_model(self):
        tts, cfg = self._settings()
        return cfg.get('model') or tts.get('model') or 'sonic-3.5'

    def list_models(self):
        return [{'id': 'sonic-3.5', 'display': 'Sonic 3.5 (pinned baseline)'}]

    def get_setup_schema(self):
        return {'name': 'Cartesia', 'badge': 'cloud', 'tag': 'paid',
                'env_vars': [{'key': 'CARTESIA_API_KEY',
                              'prompt': 'Cartesia API key',
                              'url': 'https://play.cartesia.ai/keys'}]}

    def synthesize(self, text, output_path, *, voice=None, model=None,
                   speed=None, format='mp3', **extra):
        from agent.secret_scope import get_secret
        try:
            key = get_secret('CARTESIA_API_KEY')
        except Exception:
            raise RuntimeError('Cartesia API key unavailable in this profile.') from None
        if not isinstance(key, str) or not key.strip() or '\r' in key or '\n' in key:
            raise RuntimeError('Cartesia API key missing or invalid in this profile.')
        try:
            tts, cfg = self._settings()
            voice = cfg.get('voice') or voice or tts.get('voice')
            model = cfg.get('model') or model or tts.get('model') or 'sonic-3.5'
            speed = cfg.get('speed', speed if speed is not None else tts.get('speed', 1.0))
            timeout = cfg.get('timeout', 60)
            rate = cfg.get('sample_rate', 44100)
            version = cfg.get('api_version', '2026-08-14')
            if (not isinstance(model, str) or not model.strip()
                    or isinstance(speed, bool) or not isinstance(speed, (int, float))
                    or not math.isfinite(speed) or not 0.6 <= speed <= 1.5
                    or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                    or not math.isfinite(timeout) or not 1 <= timeout <= 300
                    or type(rate) is not int or rate not in {8000, 16000, 22050, 24000, 44100, 48000}
                    or not isinstance(version, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', version)
                    or (cfg.get('language') is not None and not isinstance(cfg['language'], str))):
                raise ValueError
        except Exception:
            raise RuntimeError('Cartesia configuration invalid; check model, speed, timeout, sample_rate and api_version.') from None
        if not isinstance(voice, str) or not voice.strip():
            raise RuntimeError('Cartesia voice required; set tts.cartesia.voice to your voice ID.')
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise RuntimeError('Cartesia text must contain 1–20000 characters.')
        payload = {
            'model_id': model,
            'transcript': text,
            'voice': voice,
            'generation_config': {'speed': speed},
        }
        container = 'mp3' if format == 'mp3' else 'wav'
        output_path = str(Path(output_path).with_suffix('.' + container))
        payload['output_format'] = {'container': container, 'sample_rate': cfg.get('sample_rate', 44100)}
        payload['output_format'].update({'bit_rate': 128000} if container == 'mp3' else {'encoding': 'pcm_s16le'})
        if cfg.get('language'):
            payload['language'] = cfg['language']
        body = json.dumps(payload).encode('utf-8')
        if len(body) > 256 * 1024:
            raise RuntimeError('Cartesia request body exceeds the 256 KiB safety limit.')
        request = urllib.request.Request(
            'https://api.cartesia.ai/tts/bytes',
            data=body,
            headers={'Authorization': f'Bearer {key}',
                     'Cartesia-Version': cfg.get('api_version', '2026-08-14'),
                     'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                limit = 32 * 1024 * 1024
                mime = response.headers.get('Content-Type', '').split(';', 1)[0].lower()
                accepted = {'audio/wav', 'audio/x-wav', 'audio/wave'} if container == 'wav' else {'audio/mpeg', 'audio/mp3'}
                length = response.headers.get('Content-Length')
                if response.status != 200 or mime not in accepted or (length is not None and not 0 < int(length) <= limit):
                    raise ValueError
                audio = response.read(limit + 1)
                if not audio or len(audio) > limit or (length is not None and len(audio) != int(length)):
                    raise ValueError
                if container == 'wav':
                    if audio[:4] == b'RIFF' and audio[8:12] == b'WAVE':
                        # Streaming WAV uses unknown lengths until the response ends.
                        # Walk chunk headers (including padding), never search PCM.
                        normalized = bytearray(audio)
                        if normalized[4:8] == b'\xff' * 4:
                            normalized[4:8] = (len(audio) - 8).to_bytes(4, 'little')
                        end = min(len(audio), int.from_bytes(normalized[4:8], 'little') + 8)
                        offset = 12
                        while offset + 8 <= end:
                            size = int.from_bytes(normalized[offset + 4:offset + 8], 'little')
                            if normalized[offset:offset + 4] == b'data':
                                if size == 0xffffffff:
                                    # Unknown-sized data is the final chunk; all remaining
                                    # bounded response bytes must be whole mono16 frames.
                                    size = end - offset - 8
                                    if size % 2:
                                        raise ValueError
                                    normalized[offset + 4:offset + 8] = size.to_bytes(4, 'little')
                                break
                            offset += 8 + size + (size % 2)
                        audio = bytes(normalized)
                    with wave.open(io.BytesIO(audio), 'rb') as reader:
                        frames = reader.getnframes()
                        if (reader.getsampwidth() != 2 or reader.getframerate() != rate
                                or reader.getnchannels() != 1 or frames <= 0
                                or len(reader.readframes(frames)) != frames * 2):
                            raise ValueError
                else:
                    # Check an MPEG Layer III frame (not just an ID3 tag).
                    offset = 0
                    if audio.startswith(b'ID3'):
                        if len(audio) < 10 or any(byte & 128 for byte in audio[6:10]):
                            raise ValueError
                        offset = 10 + sum(byte << shift for byte, shift in zip(audio[6:10], (21, 14, 7, 0)))
                    header = audio[offset:offset + 4]
                    if len(header) < 4 or header[0] != 255 or header[1] & 0xe0 != 0xe0:
                        raise ValueError
                    version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
                    bitrate, sample = header[2] >> 4, (header[2] >> 2) & 3
                    if version == 1 or layer != 1 or bitrate in (0, 15) or sample == 3:
                        raise ValueError
                    rates = (44100, 48000, 32000)
                    rate_divisor = 1 if version == 3 else 2 if version == 2 else 4
                    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
                    frame_size = (144 if version == 3 else 72) * bitrates[bitrate] * 1000 // (rates[sample] // rate_divisor) + ((header[2] >> 1) & 1)
                    if len(audio) - offset < frame_size:
                        raise ValueError
        except HTTPError as error:
            error.close()
            raise RuntimeError('Cartesia request failed; check credentials, quota and connectivity.') from None
        except Exception:
            raise RuntimeError('Cartesia request failed; check credentials, quota and connectivity.') from None
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=Path(output_path).parent, prefix='.cartesia-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(audio)
            os.replace(temporary, output_path)
        except OSError:
            raise RuntimeError('Cartesia output could not be written.') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return str(output_path)


def register(ctx):
    ctx.register_tts_provider(CartesiaProvider())
