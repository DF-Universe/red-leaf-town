import asyncio
import json

import pytest
from quart import Quart

from game_host.jwt import AUD_RED_LEAF_TOWN, subject_encode
from red_leaf_town.config import get_admin_subs
from red_leaf_town.web.routes import COOKIE_NAME, create_blueprint


@pytest.mark.parametrize('contents', [None, '{', '[]', 'null', '{}', '{"admin_subs": null}', '{"admin_subs": "test-admin-sub"}', '{"admin_subs": [123]}', '{"admin_token": "test-admin-token"}'])
def test_invalid_config_denies_admin(tmp_path, monkeypatch, contents):
    path = tmp_path / 'config.json'
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setattr('red_leaf_town.config.CONFIG_PATH', path)
    assert get_admin_subs() == set()
    async def check():
        app = Quart(__name__)
        app.register_blueprint(create_blueprint())
        client = app.test_client()
        client.set_cookie('localhost', COOKIE_NAME, subject_encode('test-admin-sub', AUD_RED_LEAF_TOWN))
        response = await client.get('/api/red-leaf-town/admin/partners', headers={'X-Admin-Token': 'test-admin-token'})
        assert response.status_code == 403
    asyncio.run(check())


def test_admin_subject_whitelist_changes_immediately(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr('red_leaf_town.config.CONFIG_PATH', path)
    async def check():
        app = Quart(__name__)
        app.register_blueprint(create_blueprint())
        client = app.test_client()
        for allowed in ('first-admin', 'second-admin'):
            path.write_text(json.dumps({'admin_subs': [allowed]}))
            for subject in ('first-admin', 'second-admin', 'ordinary-user', 'impersonate:first-admin'):
                client.set_cookie('localhost', COOKIE_NAME, subject_encode(subject, AUD_RED_LEAF_TOWN))
                response = await client.get('/api/red-leaf-town/admin/partners')
                assert response.status_code == (200 if subject == allowed else 403)
        client.set_cookie('localhost', COOKIE_NAME, subject_encode('second-admin', AUD_RED_LEAF_TOWN))
        response = await client.put('/api/red-leaf-town/admin/crops', json={})
        assert response.status_code == 403
        client.delete_cookie('localhost', COOKIE_NAME)
        response = await client.get('/api/red-leaf-town/admin/partners', headers={'X-Admin-Token': 'second-admin'})
        assert response.status_code == 403
    asyncio.run(check())
