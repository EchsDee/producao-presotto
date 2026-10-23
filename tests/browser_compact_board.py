import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests


def main():
    fixture = KanbanTests()
    fixture.setUp()
    server = None
    try:
        tags = [fixture.request('POST', '/api/tags', {'name': name}).json['tag']['id'] for name in ('Corte', 'Urgente')]
        for index in range(32):
            board = fixture.board()
            response = fixture.request('POST', '/api/boards/' + fixture.board_id + '/tasks', {
                'version': board['version'], 'column_id': board['columns'][0]['id'],
                'title': 'Compact task ' + str(index), 'description': 'Full detail stays in the dialog',
                'order_number': '000AB-' + str(index), 'desired_delivery': '2025-01-01', 'tag_ids': tags,
                'assignees': [fixture.admin['id']]})
            assert response.status_code == 201, response.json
        server = make_server('127.0.0.1', 0, fixture.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 900})
            context.add_cookies([{'name': 'kanban_session', 'value': fixture.client.get_cookie('kanban_session').value, 'url': origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            expect(page.locator('.card')).to_have_count(32)
            expect(page.locator('.card').first).not_to_contain_text('Full detail')
            expect(page.locator('.card').first.locator('.task-number')).to_have_text('#1')
            expect(page.locator('.card').first.locator('.task-due')).to_have_class('task-due overdue')
            assert page.locator('.column-tasks').first.evaluate('(node) => node.scrollHeight > node.clientHeight')
            assert page.locator('.board').evaluate('(node) => node.getBoundingClientRect().bottom <= innerHeight')
            expect(page.locator('.column').first.locator('.add')).to_be_in_viewport()
            # Measure fully visible cards at common desktop/laptop resolutions.
            for width, height in ((1440, 900), (1366, 768)):
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function("() => Math.abs(parseFloat(document.querySelector('.board').style.getPropertyValue('--board-height')) - (innerHeight - document.querySelector('.board').getBoundingClientRect().top - 20)) < 2")
                metrics = page.locator('.column-tasks').first.evaluate("""(list) => {
                    const bounds = list.getBoundingClientRect();
                    return {visible: [...list.querySelectorAll('.card')].filter(card => {
                        const box = card.getBoundingClientRect();
                        return box.top >= bounds.top && box.bottom <= bounds.bottom;
                    }).length, height: bounds.height, cardHeight: list.querySelector('.card').getBoundingClientRect().height};
                }""")
                print(str(width) + 'x' + str(height) + ': ' + str(metrics))
                assert metrics['visible'] >= 10, metrics
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.locator('#search').fill('#12')
            expect(page.locator('.card')).to_have_count(1)
            expect(page.locator('.card .task-number')).to_have_text('#12')
            page.locator('#search').fill('000AB-0')
            expect(page.locator('.card')).to_have_count(1)
            page.locator('#search').fill('01/01/2025')
            expect(page.locator('.card')).to_have_count(32)
            page.locator('#search').fill('Admin')
            expect(page.locator('.card')).to_have_count(32)
            page.locator('#search').fill('#1')
            page.locator('.task-title').click()
            expect(page.locator('#task-number')).to_have_text('#1')
            expect(page.locator('#description')).to_have_value('Full detail stays in the dialog')
            expect(page.locator('#order-number')).to_have_value('000AB-0')
            page.locator('#order-number').fill('000NEW-01')
            page.locator('#cancel').click()
            expect(page.locator('#dialog')).not_to_be_visible()
            expect(page.locator('.task-order')).to_have_text('Pedido: 000NEW-01')
            page.locator('.task-title').click()
            page.locator('#desired-delivery-time').fill('14:30')
            page.locator('#cancel').click()
            expect(page.locator('#dialog')).not_to_be_visible()
            expect(page.locator('.task-due')).to_have_text('01/01 14:30')
            expect(page.locator('.task-due')).to_have_attribute('title', 'Entrega desejada: 01/01/2025 14:30')
            page.locator('#search').fill('14:30')
            expect(page.locator('.card')).to_have_count(1)
            page.locator('.task-title').click()
            expect(page.locator('#desired-delivery')).to_have_value('2025-01-01')
            expect(page.locator('#desired-delivery-time')).to_have_value('14:30')
            page.locator('#cancel').click()
            page.locator('#search').fill('')
            target = page.locator('.column').nth(1)
            page.locator('.card').first.drag_to(target)
            expect(target.locator('.card')).to_have_count(1)
            expect(target.locator('.task-number')).to_have_text('#1')
            # A held drag near a list edge must scroll that list independently.
            page.evaluate('''() => {
                const list = document.querySelector('.column-tasks'), box = list.getBoundingClientRect();
                dragged = board.tasks.find(t => t.task_number === 2).id;
                updateDragScroll({clientY: box.bottom - 2, clientX: box.left + 100}, list);
            }''')
            page.wait_for_function("() => document.querySelector('.column-tasks').scrollTop > 40")
            page.evaluate("stopDragScroll(); dragged = null; document.querySelector('.column-tasks').scrollTop = 0;")
            screenshots = Path(__file__).resolve().parents[1] / 'data/screenshots'
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / 'compact-board-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_function("() => document.querySelector('.board').getBoundingClientRect().bottom <= innerHeight")
            assert page.locator('.board').evaluate('(node) => node.scrollWidth > node.clientWidth')
            assert page.locator('.column-tasks').first.evaluate('(node) => node.clientHeight >= 130')
            expect(page.locator('.column').first.locator('.add')).to_be_in_viewport()
            page.screenshot(path=str(screenshots / 'compact-board-mobile.png'))
            # Compatibility with a server response from before migration 007.
            page.evaluate('board.tasks.forEach(task => delete task.task_number); render();')
            legacy_id = page.evaluate('board.tasks[0].id')
            page.locator('#search').fill('#' + legacy_id)
            expect(page.locator('.card')).to_have_count(1)
            expect(page.locator('.card .task-number')).to_have_text('#' + legacy_id)
            expect(page.locator('.card .task-number')).to_have_attribute('title', '#' + legacy_id)
            page.locator('.task-title').click()
            expect(page.locator('#task-number')).to_have_text('#' + legacy_id)
            expect(page.locator('#dialog')).not_to_contain_text('#undefined')
            assert not errors, errors
            browser.close()
        print('Compact cards, task/order search, detail autosave, scrolling, drag/drop and responsive layout passed.')
    finally:
        if server:
            server.shutdown()
        fixture.tearDown()


if __name__ == '__main__':
    main()
