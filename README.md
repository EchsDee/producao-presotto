# Produção Presotto

Plataforma Kanban compartilhada, com identidade visual da Presotto e interface em português brasileiro. O aplicativo mantém Flask, SQLite, autenticação, permissões e todos os recursos existentes. O tema usa azul-marinho `#08073f`, amarelo `#e4c204` e superfícies claras.

## Iniciar no Windows

Na pasta `kanban`, execute no PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe launcher.py
```

Acesse **http://127.0.0.1:8000**. Abra o endereço do servidor, em vez de abrir `dist/index.html` diretamente. As dependências já estão instaladas neste projeto. Em macOS/Linux, substitua `.venv/Scripts/python.exe` por `.venv/bin/python`.

## Atualizações pelo GitHub

Em **Configurações → Atualizações do aplicativo**, configure o proprietário, o repositório e o canal (estável ou pré-lançamentos). O padrão é **EchsDee/producao-presotto**, canal estável. Configurações já salvas por um administrador têm prioridade sobre esse padrão. A versão instalada é lida de `VERSION`. O painel permite consultar versões, ler notas, baixar o pacote validado, autorizar sua instalação e consultar o histórico. Nada é instalado automaticamente.

Execute `launcher.py` para habilitar instalações. O comando antigo `python app.py` continua funcionando para desenvolvimento, com consulta e download disponíveis; a instalação fica bloqueada nesse modo. Mantenha uma única instância do supervisor por banco e porta. Não inicie simultaneamente um segundo servidor apontando para o mesmo banco durante uma atualização. Em produção, configure seu gerenciador de serviços para executar e reiniciar **launcher.py**, mantendo o mesmo usuário do sistema e as mesmas variáveis de ambiente.

Repositórios públicos dispensam credencial. Para um repositório privado, configure **KANBAN_GITHUB_TOKEN** no ambiente do serviço, com um token de acesso refinado limitado ao repositório e permissão **Contents: read**, e reinicie o serviço. A aplicação não lê `.env` automaticamente. A credencial não é salva no banco, retornada pela API, incluída nos pacotes ou registrada nas atividades. Seu gerenciador de serviços deve conservar a configuração do ambiente entre reinicializações; ao reiniciar apenas o processo do aplicativo, o supervisor conserva esse ambiente.

As seis permissões ficam no editor existente, no grupo **Atualizações**. Administradores têm todas; demais perfis começam sem nenhuma. Consulta, download, visualização e histórico podem ser delegados individualmente. Instalação, restauração e alteração do repositório também exigem perfil **Administrador**, pois permitem trocar o código executado no servidor. Todas as verificações são feitas no backend e as operações de escrita exigem sessão e CSRF.

Neste computador, `data/updates/auth.json` contém somente `{"source":"github_cli"}` para usar a autenticação já salva pelo `gh auth login` no armazenamento de credenciais do sistema. Nenhum token é copiado para esse arquivo. Essa opção exige GitHub CLI instalado e autenticado como o mesmo usuário que executa o servidor. `KANBAN_GITHUB_TOKEN`, quando definido, tem prioridade. Novas instalações precisam configurar sua própria credencial para acessar o repositório privado; esse arquivo local não faz parte do Git nem das Releases.

### Publicar uma versão compatível

1. Atualize `VERSION` com uma versão semântica, por exemplo `1.0.1`. Preserve as migrações existentes byte a byte; adicione novas migrações numeradas em sequência. Teste-as com uma cópia do banco existente e mantenha o protocolo do supervisor compatível.
2. Execute `.venv/Scripts/python.exe build_release.py presotto-update.zip` na pasta `kanban`. O pacote contém somente código permitido, arquivos de interface, migrações, `VERSION`, `requirements.txt` e manifesto de hashes. Banco, anexos, fotos, `.env`, credenciais e ambiente virtual são excluídos.
3. Crie uma **Release** no repositório configurado, com tag correspondente (`v1.0.1`), notas e o arquivo **presotto-update.zip** como asset. Não use o ZIP de código-fonte gerado automaticamente pelo GitHub. Para pré-lançamentos, marque a Release e use uma versão como `1.1.0-beta.1`.
4. O pacote precisa ter o `digest` SHA-256 fornecido pela [API de assets do GitHub](https://docs.github.com/en/rest/releases/assets). Downloads sem esse digest são recusados. O download usa exclusivamente o asset do repositório configurado; tamanho, digest, manifesto, caminhos e hashes internos são verificados antes de preparar a versão.

O workflow `.github/workflows/release.yml` automatiza o empacotamento e a publicação ao enviar uma tag `vX.Y.Z`. Ele verifica se a tag corresponde a `VERSION`, publica `presotto-update.zip` e marca tags com sufixo como pré-lançamento. Para publicar a próxima versão, altere `VERSION`, faça commit, crie a tag correspondente e envie a branch e a tag para o GitHub. O arquivo `.gitattributes` mantém as quebras de linha iguais no Windows e no Linux para preservar os hashes e a compatibilidade dos pacotes.

O protocolo inicial aceita pacotes de até 64 MB comprimidos/descomprimidos e exige **requirements.txt idêntico ao instalado**. Versões que alteram dependências exigem atualização assistida do ambiente pelo operador; o painel informa a incompatibilidade e não executa `pip`, scripts de instalação ou comandos enviados pelo navegador. A consulta considera as primeiras 100 Releases publicadas, escolhendo a maior versão semântica elegível no canal. O campo `minimum_version` do manifesto permite exigir uma versão intermediária.

### Instalação, recuperação e arquivos persistentes

O supervisor é separado do processo Flask/Waitress e continua vivo durante a troca. Código novo fica em versões separadas dentro de `data/updates/releases`; o caminho absoluto de **KANBAN_DATABASE** é mantido entre versões. Anexos e fotos permanecem no SQLite. A instalação pausa novas gravações, para o servidor, faz backup consistente do banco e copia `.env`, quando presente, para a área de recuperação. O ambiente do serviço continua sob responsabilidade do gerenciador de serviços.

Um diário persistente permite recuperar uma instalação interrompida. A nova versão executa as migrações existentes e precisa responder à verificação autenticada de saúde com a versão esperada e relacionamentos válidos. Só então a instalação é marcada como concluída. Falhas recuperam o código e o banco anteriores; uma cópia do banco que falhou também é guardada para diagnóstico. Durante essa janela não são aceitas gravações de usuários, evitando perder alterações na recuperação. Notas de Release são exibidas como texto, sem executar HTML.

**Restaurar versão anterior** reinicia o servidor com o código anterior e mantém o banco atual, inclusive tarefas criadas após a atualização. Por segurança, o botão só funciona quando o esquema atual corresponde exatamente ao esquema guardado para a versão anterior. Se houve mudança de esquema, a restauração pelo painel é bloqueada; um operador precisa planejar a compatibilidade e a migração dos dados atuais. Não restaure um banco antigo sobre dados recentes sem esse planejamento.

Arquivos relativos ao diretório do banco:

- `updates/state.json`, `download.json`, `progress.json`: consulta, pacote preparado e progresso.
- `updates/history.json`: histórico persistente das instalações e recuperações; resultados também ficam no registro de atividades do banco.
- `updates/active.json`, `previous.json`, `journal.json`: versão ativa, versão anterior e recuperação pendente.
- `updates/before-*.sqlite3`, `failed-*.sqlite3`: backups de instalação e diagnóstico.
- `updates/server.log`: saída do servidor gerenciado.

Restrinja o diretório do banco e seus backups ao usuário do serviço: eles contêm dados e a chave interna de saúde do supervisor. Esses arquivos nunca são servidos pelo aplicativo. Não apague `updates` enquanto usa uma versão instalada pelo painel, pois o código ativo pode estar nesse diretório. Faça backup externo do banco, dessa pasta e da configuração do serviço. O histórico e as cópias anteriores não são removidos automaticamente.

Não foi necessária uma migração de esquema para este recurso: a configuração usa a tabela `metadata` existente e os eventos usam `activity_log`, sem alterar registros ou decisões de permissão anteriores.

Validação do atualizador: `python -m unittest discover -s tests -q` inclui instalação real com reinicialização, falha de versão, recuperação após interrupção, restauração preservando tarefas recentes, autorização, integridade, caminhos e credenciais. `python tests/browser_updates.py` verifica o painel no Edge e o layout móvel com banco temporário.

Em um banco ainda não inicializado, o primeiro acesso é **Monke / monkeman**, com perfil de Administrador e alteração obrigatória de senha. Esse é o acesso inicial de instalações novas; não substitui senhas já alteradas ou redefinidas. Senhas novas devem ter de 10 a 256 caracteres. O marcador de inicialização impede recriar a conta ou restaurar a senha inicial ao reiniciar.

## Interface e identidade visual

- Cabeçalho azul-marinho com logotipo local da Presotto e ações principais em amarelo.
- Quadros, cartões, filtros, formulários, administração e registro de atividades em português.
- Perfis exibidos como Administrador, Gestor, Membro e Leitor; os identificadores internos permanecem iguais para compatibilidade.
- Datas e números formatados em `pt-BR`, respeitando o fuso horário do navegador.
- Permissões agrupadas por área para facilitar a configuração.
- Layout responsivo, foco visível e suporte à preferência por movimento reduzido.

O logotipo adaptado para Presotto fica em `dist/assets/presotto-logo.png` e é servido localmente. O tema fica em `dist/theme.css`. Não há etapa de compilação nem dependências visuais externas.

A tradução não reescreve nomes, descrições, arquivos ou registros históricos cadastrados pelos usuários. Rótulos padrão da versão anterior são apresentados em português; suas informações originais continuam no banco.

## Armazenamento e alterações simultâneas

Em **Configurações → Aparência**, administradores podem alterar as cores do cabeçalho, dos botões e do fundo. A escolha mostra uma prévia local; **Salvar cores** aplica o tema a todos os usuários e à tela de login. É possível cancelar a prévia ou restaurar as cores da Presotto antes de salvar. O texto dos botões e do cabeçalho ajusta seu contraste. O tema é armazenado na tabela `metadata`, com controle de versão e registro de atividade. A autorização é verificada no backend; delegar gerenciamento de usuários não concede acesso à alteração das cores.

O banco padrão é `data/kanban.sqlite3`, relativo à pasta de `app.py`. Ele armazena quadros, colunas, tarefas, posições, responsáveis, contas, permissões, sessões, atividades e anexos. Os arquivos são BLOBs do SQLite; não existe pasta pública de uploads.

Detalhes de tarefas existentes são salvos após 900 ms sem digitação. Criações e configurações são persistidas ao enviar seus formulários. Movimentações, atribuições, exclusões e envios de arquivos são salvos imediatamente. Fechar os detalhes da tarefa salva alterações pendentes. Se ocorrer erro, o formulário e o rascunho são mantidos.

As gravações usam transações `BEGIN IMMEDIATE`, chaves estrangeiras e controle de versão. O modo WAL permite leitura enquanto outro usuário grava. Uma alteração baseada em versão desatualizada recebe HTTP 409, sem substituir o trabalho já salvo. O controle é por quadro: alterações simultâneas em tarefas diferentes também podem exigir atualização. A administração usa versões individuais por conta. Quadros são atualizados a cada cinco segundos quando não há edição em andamento.

As permissões valem para todos os quadros deste espaço. Não existe isolamento por empresa ou associação de usuários por quadro. Setas reordenam colunas; arrastar um cartão sobre outro o insere antes dele; soltar na coluna o coloca no final. Ao excluir uma coluna, é preciso escolher o destino de suas tarefas. Os anexos acompanham as tarefas. Deve permanecer pelo menos um quadro e uma coluna.

## Perfis e permissões

### Meu perfil e avatares

Clique no avatar/nome no cabeçalho e escolha **Meu perfil**. Qualquer usuário autenticado pode alterar seu próprio nome de exibição, enviar/trocar/remover a foto e acessar a alteração de senha. O nome de exibição não altera o usuário de login, o ID interno, as permissões nem as atribuições. O menu do perfil oferece **Meu perfil** e **Sair**. A administração permanece no menu principal, conforme as permissões da conta.

Fotos aceitas: JPG/JPEG, PNG e WebP, até 5 MB e 16 megapixels. A imagem é validada e decodificada por Pillow, corrigida conforme a orientação EXIF, recortada ao centro e armazenada como WebP de 256 × 256 pixels, sem copiar metadados. Imagens animadas, inválidas ou excessivamente grandes são recusadas. A prévia é local até clicar em **Salvar perfil**. Sem foto, aparecem as iniciais do nome.

Fotos são BLOBs no banco, com URLs autenticadas e revisão para atualização de cache. Um usuário pode ler sua própria foto; fotos de outros usuários exigem acesso a quadros e visualização de responsáveis, ou perfil de Administrador. Não há pasta pública de avatares. Em **Administração → Gerenciar → Editar perfil**, administradores podem editar nome/foto de outras pessoas; a permissão delegada de gerenciar contas não concede esse acesso.

Os cartões mostram avatares dos responsáveis, com nome ao passar o mouse. Até quatro pessoas são exibidas; acima disso, aparecem três avatares e `+N`. Os detalhes mostram todos os responsáveis e oferecem seleção com foto e nome. A edição continua sujeita a **Atribuir usuários**, **Atribuir a si mesmo** e **Visualizar responsáveis**. Os avatares dos cartões não oferecem ações de edição.

Alterações de nome e foto são registradas na mesma transação. Alterações e redefinições de senha registram apenas que ocorreram. O histórico mantém IDs permanentes, apresenta nomes de exibição atuais para contas existentes e conserva os nomes registrados para contas excluídas. Nenhuma foto ou credencial é incluída nos registros.

Alterar senha exige a senha atual, nova senha de 10–256 caracteres e confirmação. A API `POST /api/password` recebe `current_password`, `new_password` e `confirm_password`. O perfil usa `GET/PATCH /api/profile`; a edição usa versão independente (`profile_version`) para recusar alterações simultâneas desatualizadas. O PATCH aceita JSON para o nome ou multipart com `display_name`, `version`, `avatar_action` (`keep`, `replace`, `remove`) e uma imagem no campo `avatar` quando necessário.

A migração `003_user_profiles.sql` adiciona os campos de perfil e inicia o nome de exibição com o usuário existente. Contas, hashes de senha, sessões, permissões, atividades e atribuições são mantidos. Atualize as dependências com `python -m pip install -r requirements.txt` antes de iniciar a versão nova; Pillow é a única dependência adicional.

| Perfil | Permissões padrão |
| --- | --- |
| Administrador | Acesso completo, incluindo contas e permissões |
| Gestor | Gerenciar tarefas e colunas, atribuir responsáveis, gerenciar arquivos e consultar atividades |
| Membro | Criar, editar e mover tarefas; atribuir a si mesmo; enviar e baixar arquivos; excluir os próprios arquivos; consultar responsáveis e histórico |
| Leitor | Consultar quadros, tarefas, responsáveis e histórico; baixar arquivos |

Os valores internos dos perfis são `Administrator`, `Manager`, `Member` e `Viewer`. Não os altere diretamente no código ou no banco para traduzir a interface.

Em **Administração**, usuários autorizados podem criar contas, alterar perfis, configurar permissões individuais, ativar/desativar contas, redefinir senhas e excluir usuários. **Restaurar padrão do perfil** remove as diferenças em relação ao perfil selecionado. Administradores mantêm acesso completo.

Gerenciar contas e gerenciar permissões são direitos separados. Um gestor autorizado a administrar usuários pode criar Membros, redefinir senhas e administrar contas comuns; alterar perfis ou permissões exige a autorização correspondente. Apenas administradores podem conceder ou modificar o perfil de Administrador.

Não é permitido modificar a própria conta pela administração, nem excluir/desativar/rebaixar o último administrador ativo. Para mudar a própria senha, use **Meu perfil → Alterar minha senha**. Alterações administrativas invalidam as sessões da conta afetada. Excluir uma pessoa mantém suas tarefas e arquivos, remove seus vínculos de responsável e preserva seu nome nos registros de atividades anteriores.

| Permissão | Identificador | Admin. | Gestor | Membro | Leitor |
| --- | --- | --- | --- | --- | --- |
| Visualizar registro de atividades | `view_activity_log` | Sim | Sim | Não | Não |
| Visualizar histórico das tarefas | `view_task_history` | Sim | Sim | Sim | Sim |
| Enviar arquivos | `upload_attachments` | Sim | Sim | Sim | Não |
| Visualizar e baixar arquivos | `download_attachments` | Sim | Sim | Sim | Sim |
| Excluir quaisquer arquivos | `delete_attachments` | Sim | Sim | Não | Não |
| Excluir os próprios arquivos | `delete_own_attachments` | Sim | Sim | Sim | Não |
| Atribuir usuários às tarefas | `assign_users` | Sim | Sim | Não | Não |
| Atribuir tarefas a si mesmo | `assign_self` | Sim | Sim | Sim | Não |
| Visualizar responsáveis | `view_task_assignees` | Sim | Sim | Sim | Sim |

Todas as permissões são verificadas no servidor. Enviar arquivos não concede exclusão. Excluir um arquivo exige autorização geral ou autorização sobre arquivos enviados pelo próprio usuário autenticado. Excluir tarefas/quadros também valida a exclusão dos arquivos contidos, evitando contornar as permissões pela exclusão do item principal.

Atribuições são independentes da edição de detalhes. A API valida os IDs realmente adicionados e removidos. Omitir responsáveis ao editar uma tarefa mantém suas atribuições. `PATCH /api/tasks/<id>/assignees` aceita `add`, `remove` e a versão do quadro. Usuários autorizados a alterar atribuições, mas sem permissão para consultá-las, recebem controles explícitos de adição/remoção sem acesso à lista atual. O filtro de responsável e **Minhas tarefas** ajudam a localizar o trabalho de cada pessoa.

## Registro de atividades

**Atividades** abre o registro global, com filtros por usuário, ação, tarefa e período. A data final inclui todo o dia selecionado no fuso do navegador. O histórico é paginado e mostra primeiro as atividades mais recentes. **Histórico da tarefa** aparece nos detalhes quando autorizado. As duas permissões de consulta são independentes.

São registrados criação, edição, movimentação, reordenação e exclusão de tarefas/colunas/quadros; adição e remoção de responsáveis; envio e exclusão de arquivos; criação, alteração e exclusão de contas; alterações de perfil e permissões. Exclusões em cascata e importações também são registradas.

Cada registro guarda o autor, o item afetado, contexto do quadro/tarefa, instante UTC com frações de segundo e valores anteriores/novos pertinentes. **O que mudou** apresenta os detalhes em português. A gravação ocorre na mesma transação da alteração; falhas, conflitos e operações recusadas não produzem registros de sucesso. Os registros sobrevivem à exclusão dos itens ou das contas e não podem ser modificados ou excluídos pela API.

Senhas, hashes de senha, sessões, tokens e conteúdos dos anexos não são registrados. Os eventos de arquivos preservam nome, formato, tamanho, SHA-256 e dados de envio. Negar acesso a responsáveis oculta os respectivos eventos e IDs; negar download também oculta metadados/eventos de arquivos. O histórico começa na instalação desse recurso, sem reconstruir ações anteriores.

## Arquivos anexados

Salve a tarefa e abra seus detalhes para enviar arquivos. São aceitos PDF, DOC/DOCX, XLS/XLSX, TXT em UTF-8, PNG, JPG/JPEG, ZIP e DXF ASCII/binário. O servidor valida extensão, conteúdo básico do formato e tamanho. Os arquivos nunca são executados, extraídos ou servidos como HTML; downloads exigem sessão e permissão e usam `nosniff`. Essa validação não substitui antivírus.

O limite padrão é 20 MiB por arquivo e até dez arquivos por envio. A requisição multipart tem uma margem adicional de 1 MiB; envie arquivos grandes individualmente. Uma falha desfaz todo o lote. DXFs são preservados byte a byte, com download confiável; não há visualização CAD integrada.

## Configuração e implantação

Defina variáveis no terminal ou no serviço. `.env.example` é apenas referência; o aplicativo não carrega `.env` automaticamente.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `KANBAN_DATABASE` | `<projeto>/data/kanban.sqlite3` | Banco em disco persistente; prefira caminho absoluto |
| `KANBAN_HOST` | `127.0.0.1` | Endereço de escuta |
| `KANBAN_PORT` | `8000` | Porta |
| `KANBAN_MAX_FILE_MB` | `20` | Limite de arquivo em MiB |
| `KANBAN_SESSION_HOURS` | `12` | Prazo absoluto da sessão |
| `KANBAN_COOKIE_SECURE` | `0` | Use `1` com HTTPS |
| `KANBAN_PUBLIC_ORIGIN` | Origem da requisição | Origem pública exata; configure atrás de proxy HTTPS |

```powershell
$env:KANBAN_COOKIE_SECURE = "1"
$env:KANBAN_PUBLIC_ORIGIN = "https://kanban.exemplo.com"
$env:KANBAN_DATABASE = "D:/kanban-data/kanban.sqlite3"
.venv/Scripts/python.exe app.py
```

O aplicativo usa Waitress sem modo de depuração. Em produção, use proxy HTTPS preservando o cabeçalho Host e disco persistente. Cookies são HttpOnly e SameSite=Strict; gravações exigem token CSRF vinculado à sessão. Senhas usam Argon2id, tokens de sessão são armazenados como hashes e há limitação persistente de tentativas de acesso. Sessões permanecem válidas após reinícios, até expirar ou serem revogadas.

Restrinja o acesso ao banco à conta do serviço. Backups contêm arquivos privados, hashes de senhas e sessões. SQLite atende a esta implantação em um servidor; não use WAL em compartilhamento de rede nem bancos separados por réplica. Escala horizontal exigiria um banco compartilhado, como PostgreSQL.

O antigo `.openai/hosting.json` se refere à versão estática anterior. Hospedagem apenas estática não executa este backend Python. Implante `app.py`, suas dependências e `dist/` juntos.

## Importação, migrações e backup

**Importar quadro**, ao final do quadro, permite copiar dados antigos de localStorage ou importar um JSON. A origem é mantida; a importação é transacional e repetidas importações da mesma cópia pela mesma conta não duplicam o quadro.

Dados do navegador são separados por origem. Se a versão antiga era aberta por `file://` ou em outro endereço/porta, abra o endereço original e execute no console do navegador para exportar:

```javascript
const antigo = JSON.parse(localStorage.getItem('everyday-kanban-board-v2') || 'null');
const padrao = [['todo','A fazer','todo'],['doing','Em andamento','doing'],['done','Concluído','done']];
const dados = antigo || {
  columns: padrao.concat(JSON.parse(localStorage.getItem('everyday-kanban-columns-v1') || '[]').map(c => [c[0],c[1],'todo'])),
  tasks: JSON.parse(localStorage.getItem('everyday-kanban-v1') || '[]')
};
const link = document.createElement('a');
link.href = URL.createObjectURL(new Blob([JSON.stringify(dados)], {type:'application/json'}));
link.download = 'quadro.json'; link.click();
setTimeout(() => URL.revokeObjectURL(link.href), 1000);
```

Migrações SQL numeradas em `migrations/` são aplicadas automaticamente em transação, com controle por `PRAGMA user_version`. Acrescente novas migrações em vez de alterar as já aplicadas. A migração 002 preserva decisões explícitas de permissões e copia antigas autorizações combinadas para os novos direitos correspondentes quando necessário. A atualização visual/pt-BR não exige migração e não reescreve dados existentes.

Para criar um backup consistente, inclusive com o servidor ligado:

```powershell
.venv/Scripts/python.exe backup.py backups/kanban-2026-10-21.sqlite3
```

O arquivo de destino deve ser novo. Para restaurar, pare o servidor, mantenha os dados antigos guardados e aponte `KANBAN_DATABASE` para uma cópia do backup em um novo diretório. Não substitua um banco em uso nem misture arquivos WAL/SHM antigos com um banco restaurado. Faça backup antes das atualizações.

## Testes

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --check dist/app.js
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe tests/browser_smoke.py
.venv/Scripts/python.exe tests/browser_permissions.py
.venv/Scripts/python.exe tests/browser_theme.py
.venv/Scripts/python.exe tests/browser_profiles.py
```

Os testes usam bancos temporários. Cobrem autenticação, permissões, sessões, concorrência, migrações, preservação de dados, registros de atividades, arquivos, atribuições e restauração de backup. Os testes de navegador usam Microsoft Edge instalado e também verificam tradução, identidade visual, responsividade, formulários, filtros e arrastar/soltar. O teste visual salva imagens com dados demonstrativos em `data/previews/`, sem usar informações reais do banco.
