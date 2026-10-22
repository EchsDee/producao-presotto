import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests


def main():
    fixture = KanbanTests(); fixture.setUp()
    server = make_server('127.0.0.1', 0, fixture.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        response = fixture.app.test_client().get('/version')
        assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
        assert len(response.json['revision']) == 64
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(locale='pt-BR')
            context.add_cookies([{'name': 'kanban_session', 'value': fixture.client.get_cookie('kanban_session').value, 'url': origin}])
            page = context.new_page(); errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin); expect(page.locator('#workspace')).to_be_visible()
            assert page.locator('meta[name="app-build"]').get_attribute('content') == response.json['revision']
            page.evaluate('checkApplicationVersion()')
            expect(page.locator('#version-notice')).not_to_be_visible()
            page.route('**/version', lambda route: route.abort())
            page.evaluate('checkApplicationVersion()')
            expect(page.locator('#version-notice')).not_to_be_visible()
            page.unroute('**/version')
            page.locator('#new').click(); page.locator('#title').fill('Rascunho preservado')
            page.route('**/version', lambda route: route.fulfill(json={'version': '1.0.3', 'revision': 'new-build'}))
            page.evaluate('checkApplicationVersion()')
            assert page.locator('#version-notice').evaluate('(node) => !node.hidden')
            expect(page.locator('#title')).to_have_value('Rascunho preservado')
            page.evaluate('pending = true; document.querySelector("#reload-version").click(); pending = false;')
            expect(page.locator('#version-reload-hint')).to_contain_text('Aguarde')
            confirmations = []
            page.on('dialog', lambda dialog: (confirmations.append(dialog.message), dialog.dismiss()))
            page.evaluate('document.querySelector("#reload-version").click()')
            assert confirmations and 'não salvas' in confirmations[0]
            expect(page.locator('#title')).to_have_value('Rascunho preservado')
            page.locator('#cancel').click()
            expect(page.locator('#version-notice')).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            box = page.locator('#version-notice').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= 390
            page.unroute('**/version')
            with page.expect_navigation():
                page.locator('#reload-version').click()
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#version-notice')).not_to_be_visible()
            assert not errors, errors
            browser.close()
        print('PASS: version identity, no-cache, background detection, network failures, draft protection, mobile notice and reload')
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__ == '__main__':
    main()
