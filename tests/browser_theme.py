"""Visual and Portuguese locale checks, using sample data in a disposable database."""
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
    output = Path(__file__).resolve().parents[1] / 'data' / 'previews'
    output.mkdir(parents=True, exist_ok=True)
    try:
        mariana = fixture.create_user('Mariana', 'Member')
        rafael = fixture.create_user('Rafael', 'Manager')
        fixture.request('PATCH', '/api/boards/' + fixture.board_id,
                        {'name': 'Produção e projetos', 'version': fixture.board()['version']})
        samples = [
            (0, 'Revisar desenho do suporte lateral', 'Conferir as medidas e os pontos de fixação antes de liberar o corte.', 'high', [mariana['id']]),
            (0, 'Preparar arquivos para corte a laser', 'Separar os arquivos DXF e organizar as peças por espessura.', 'normal', [rafael['id']]),
            (0, 'Conferir disponibilidade de materiais', 'Verificar o estoque de chapas para os próximos pedidos.', 'low', []),
            (1, 'Produzir lote de peças dobradas', 'Acompanhar a produção e validar a primeira peça do lote.', 'high', [rafael['id']]),
            (1, 'Atualizar programação da semana', 'Alinhar os prazos de entrega com a equipe.', 'normal', [fixture.admin['id']]),
            (2, 'Aprovar projeto de estrutura metálica', 'Desenhos revisados e aprovados para produção.', 'normal', [mariana['id']]),
            (2, 'Organizar documentação técnica', 'Documentos e especificações reunidos por projeto.', 'low', [fixture.admin['id']]),
        ]
        for col,title,description,priority,people in samples:
            board = fixture.board()
            result = fixture.request('POST', '/api/boards/' + fixture.board_id + '/tasks', {
                'version': board['version'], 'column_id': board['columns'][col]['id'], 'title': title,
                'description': description, 'priority': priority, 'assignees': people})
            assert result.status_code == 201
        server = make_server('127.0.0.1', 0, fixture.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width':1440,'height':1000}, locale='pt-BR')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            expect(page.locator('#login-screen')).to_be_visible()
            expect(page.locator('html')).to_have_attribute('lang','pt-BR')
            assert page.locator('.brand img').evaluate('(img)=>img.complete&&img.naturalWidth>0')
            assert page.locator('.topbar').evaluate('(e)=>getComputedStyle(e).backgroundColor') == 'rgb(8, 7, 63)'
            page.screenshot(path=str(output/'login-desktop.png'), full_page=True)
            context.add_cookies([{'name':'kanban_session','value':fixture.client.get_cookie('kanban_session').value,
                                 'url':origin,'httpOnly':True,'sameSite':'Strict'}])
            page.reload()
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#profile-role')).to_have_text('Administrador')
            expect(page.locator('#summary')).to_contain_text('7 tarefas')
            expect(page.locator('.column')).to_have_count(3)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/'quadro-desktop.png'), full_page=True)
            page.locator('#activity-menu').click()
            expect(page.locator('#activity-screen')).to_be_visible()
            expect(page.locator('#activity-list')).to_contain_text('criou a tarefa')
            page.locator('.log-entry details').first.click()
            expect(page.locator('.log-entry pre').first).to_contain_text('Antes')
            page.screenshot(path=str(output/'atividades-desktop.png'), full_page=True)
            page.locator('#settings').click()
            expect(page.locator('#configuration-screen')).to_be_hidden()
            expect(page.locator('#appearance')).to_be_hidden()
            expect(page.locator('#updates-panel')).to_be_hidden()
            page.locator('.user-row').filter(has_text='Mariana').get_by_role('button',name='Gerenciar').click()
            expect(page.locator('#user-role')).to_have_value('Member')
            expect(page.locator('#user-role option:checked')).to_have_text('Membro')
            expect(page.locator('#permissions legend')).to_have_count(6)
            expect(page.locator('#permissions')).to_contain_text('Excluir os próprios arquivos')
            page.screenshot(path=str(output/'permissoes-desktop.png'), full_page=True)
            page.locator('#user-cancel').click()
            page.locator('#board-menu').click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/'quadro-celular.png'), full_page=True)
            page.get_by_role('button',name='Detalhes / Editar',exact=True).first.click()
            expect(page.locator('#task-history')).to_contain_text('criou a tarefa')
            assert page.locator('#dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
            page.locator('#cancel').click()
            page.locator('#configuration-menu').click()
            expect(page.locator('#configuration-screen')).to_be_visible()
            expect(page.locator('#admin')).to_be_hidden()
            expect(page.locator('#users-list')).to_be_hidden()
            expect(page.locator('#appearance')).to_be_visible()
            page.locator('#theme-navy').evaluate("e=>{e.value='#252558';e.dispatchEvent(new Event('input',{bubbles:true}))}")
            assert page.locator('.topbar').evaluate('(e)=>getComputedStyle(e).backgroundColor') == 'rgb(37, 37, 88)'
            with page.expect_response(lambda r:r.url.endswith('/api/theme') and r.request.method=='PATCH') as saved:
                page.locator('#theme-form .primary').click()
            assert saved.value.status == 200
            page.reload()
            expect(page.locator('#workspace')).to_be_visible()
            assert page.locator('.topbar').evaluate('(e)=>getComputedStyle(e).backgroundColor') == 'rgb(37, 37, 88)'
            assert not errors, errors
            browser.close()
        print('PASS: tema Presotto, logotipo local, pt-BR, perfis/permissões traduzidos, histórico, desktop e celular')
        print('Previews: '+str(output))
    finally:
        if server:
            server.shutdown();thread.join(timeout=5);server.server_close()
        fixture.tearDown()


if __name__ == '__main__':
    main()
