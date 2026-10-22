import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect,sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests


def main():
    fixture=KanbanTests();fixture.setUp()
    server=make_server('127.0.0.1',0,fixture.app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(locale='pt-BR')
            context.add_cookies([{'name':'kanban_session','value':fixture.client.get_cookie('kanban_session').value,'url':origin,'httpOnly':True,'sameSite':'Strict'}])
            page=context.new_page();errors=[];dialogs=[];uploads=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('dialog',lambda dialog:(dialogs.append(dialog.message),dialog.dismiss()))
            page.on('request',lambda request:uploads.append(request.url) if request.method=='POST' and request.url.endswith('/attachments') else None)
            page.goto(origin);page.wait_for_function('() => user && !pending')
            page.locator('#new').click()
            expect(page.locator('#upload-form')).to_be_visible()
            page.locator('#files').set_input_files([
                {'name':'desenho.dwg','mimeType':'application/octet-stream','buffer':b'AC1032\x00\xff'},
                {'name':'arquivo.rar','mimeType':'application/octet-stream','buffer':b'Rar!\x00\xff'}])
            expect(page.locator('#queued-files .attachment')).to_have_count(2)
            assert not uploads and not fixture.board()['tasks']
            page.locator('#title').fill('Tarefa com anexos')
            page.locator('#save-task').click()
            expect(page.get_by_role('link',name='desenho.dwg')).to_be_visible()
            expect(page.get_by_role('link',name='arquivo.rar')).to_be_visible()
            page.wait_for_function('() => !pending')
            assert len(fixture.board()['tasks'])==1
            assert len(fixture.board()['tasks'][0]['attachments'])==2
            expect(page.locator('#queued-files .attachment')).to_have_count(0)
            attempts=[]
            def fail_second(route):
                attempts.append(route.request.url)
                if len(attempts)==2:route.fulfill(status=503,json={'error':'Falha simulada.'})
                else:route.continue_()
            page.route('**/api/tasks/*/attachments',fail_second)
            page.locator('#files').set_input_files([
                {'name':'primeiro.dce','mimeType':'application/octet-stream','buffer':b'first'},
                {'name':'segundo.bin','mimeType':'application/octet-stream','buffer':b'second'}])
            expect(page.get_by_role('link',name='primeiro.dce')).to_be_visible()
            expect(page.locator('#retry-upload')).to_be_visible()
            expect(page.locator('#queued-files')).to_contain_text('segundo.bin')
            assert len(fixture.board()['tasks'][0]['attachments'])==3
            page.locator('#retry-upload').click()
            expect(page.get_by_role('link',name='segundo.bin')).to_be_visible()
            page.wait_for_function('() => !pending')
            expect(page.locator('#queued-files .attachment')).to_have_count(0)
            attachments=fixture.board()['tasks'][0]['attachments']
            assert len(attachments)==4
            assert len([a for a in attachments if a['filename']=='primeiro.dce'])==1
            for name,expected in [('desenho.dwg',b'AC1032\x00\xff'),('arquivo.rar',b'Rar!\x00\xff')]:
                item=next(a for a in attachments if a['filename']==name)
                assert fixture.client.get('/api/attachments/'+item['id']+'/download').data==expected
            page.locator('#files').set_input_files({'name':'grande.bin','mimeType':'application/octet-stream','buffer':b'x'*4097})
            expect(page.locator('#notice')).to_contain_text('grande.bin')
            assert len(fixture.board()['tasks'][0]['attachments'])==4
            assert not dialogs,dialogs
            assert not errors,errors
            browser.close()
        print('PASS: automatic upload, creation with files, partial failure retry, no duplicate task/files, limits and original bytes')
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close();fixture.tearDown()


if __name__=='__main__':main()
