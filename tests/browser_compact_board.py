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
            page.clock.install()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            expect(page.locator('.card')).to_have_count(32)
            expect(page.locator('.card').first).not_to_contain_text('Full detail')
            expect(page.locator('.card').first.locator('.task-number')).to_have_text('#1')
            expect(page.locator('.card').first.locator('.task-due')).to_have_class('task-due overdue')
            expect(page.locator('.card').first).to_have_class('card overdue')
            colors = page.locator('.card').first.evaluate("""card => ({
                border: getComputedStyle(card).borderTopColor,
                number: getComputedStyle(card.querySelector('.task-number')).color,
                date: getComputedStyle(card.querySelector('.task-due')).color
            })""")
            assert colors['border'] == colors['number'] == colors['date'] == 'rgb(181, 67, 73)', colors

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
            rows = page.locator('#task-date, #desired-delivery, #desired-delivery-time').evaluate_all(
                '(inputs) => inputs.map(input => ({top: input.getBoundingClientRect().top, height: input.getBoundingClientRect().height}))')
            assert max(row['top'] for row in rows) - min(row['top'] for row in rows) <= 1, rows
            assert all(row['height'] == 46 for row in rows), rows

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
            expect(page.locator('#desired-delivery')).to_have_value('01/01/2025')
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
            # Laptop screens with browser chrome and 125% display scaling.
            for width, height in ((1366, 768), (1366, 650), (1093, 614), (1024, 600)):
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function("() => document.querySelector('.board').getBoundingClientRect().bottom <= innerHeight")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, height)
                expect(page.locator('.column').first.locator('.add')).to_be_in_viewport()
                expect(page.locator('#new')).to_be_in_viewport()
                overlaps = page.locator('.card').evaluate_all("""cards => cards.some(card => {
                    const left = card.querySelector('.card-metadata')?.getBoundingClientRect();
                    const right = card.querySelector('.card-bottom')?.getBoundingClientRect();
                    return left && right && left.right > right.left + 1;
                })""")
                assert not overlaps, (width, height)
                page.screenshot(path=str(screenshots / ('laptop-' + str(width) + 'x' + str(height) + '.png')))
            page.set_viewport_size({'width': 1366, 'height': 768})
            assert page.evaluate("() => !deliveryIsOverdue('2026-10-23T14:30', false, new Date(2026,9,23,14,29))")
            assert page.evaluate("() => deliveryIsOverdue('2026-10-23T14:30', false, new Date(2026,9,23,14,31))")
            assert page.evaluate("() => !deliveryIsOverdue('2026-10-23T14:30', true, new Date(2026,9,23,15,0))")
            assert page.evaluate("() => !deliveryIsOverdue('2026-10-23', false, new Date(2026,9,23,23,59))")
            instant = page.evaluate('new Date(2026,9,23,14,29,55).getTime()')
            page.clock.set_system_time(time=instant / 1000)
            page.evaluate("board.tasks.find(t => t.task_number === 1).desired_delivery = '2026-10-23T14:30'; render();")
            timed_card = page.locator('.card[data-task-number="1"]')
            expect(timed_card).not_to_have_class('card overdue')
            page.clock.fast_forward(20000)
            expect(timed_card).to_have_class('card overdue')
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
