"""Hermes dashboard routes; management is local-only and never synthesizes."""
import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException
from hermes_constants import get_hermes_home, profile_name_for_home
from hermes_cli.config import load_config, save_config

# Hermes imports this file as a standalone module (not a child of the native
# plugin package). Load only our fixed sibling file, without altering sys.path
# or importing/registering the synthesis provider a second time.
_spec = importlib.util.spec_from_file_location(
    __name__ + '_favorites', Path(__file__).resolve().parents[1] / 'favorites.py')
assert _spec is not None and _spec.loader is not None
_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store)
router = APIRouter()


def _profile():
    profile = profile_name_for_home(get_hermes_home())
    if profile is None:
        raise HTTPException(409, 'Cannot identify backend profile.')
    return profile


def _guard(payload, fields):
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise HTTPException(422, 'Invalid request fields.')
    if payload.get('expected_profile') != _profile():
        raise HTTPException(409, 'Backend profile changed; reload favorites.')


def _state():
    tts = load_config().get('tts', {})
    cfg = tts.get('cartesia', {})
    return {'favorites': _store.read_favorites(), 'profile': _profile(),
            'selection': {'voice_id': cfg.get('voice') or tts.get('voice') or None,
                          'language': cfg.get('language') or None,
                          'provider': tts.get('provider') or 'edge'},
            'catalog_scope': 'shared-local-user'}


@router.get('/favorites')
def get_favorites():
    try:
        return _state()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.put('/favorites')
def put_favorites(payload: dict):
    _guard(payload, ('favorites', 'expected_profile'))
    try:
        _store.write_favorites(payload['favorites'])
        return _state()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post('/selection')
def select_favorite(payload: dict):
    _guard(payload, ('voice_id', 'expected_profile'))
    try:
        favorites = _store.read_favorites()
        favorite = next((item for item in favorites
                         if item['voice_id'] and item['voice_id'] == payload['voice_id']), None)
        if favorite is None:
            raise ValueError('Select a saved favorite voice ID.')
        config = load_config()
        config.setdefault('tts', {}).setdefault('cartesia', {}).update(
            voice=favorite['voice_id'], language=favorite['language'])
        save_config(config)
        return _state()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
