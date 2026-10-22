'use strict';
let updateInfo = null, updateWatching = false;
let tagRegistry = [], selectedTaskTags = new Set(), tagFilters = new Set(), editingTag = null;
function hasTagManagement() { return ['create_tags','edit_tags','delete_tags','manage_tag_status'].some(can); }
function hasAdminAccess() { return can('manage_users') || can('manage_permissions') || hasTagManagement(); }
function tagChip(tag) {
  const chip = el('span','task-tag',tag.name);
  chip.style.backgroundColor = tag.color;
  const rgb = [1,3,5].map(i=>parseInt(tag.color.slice(i,i+2),16)/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);
  chip.style.color = rgb.reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0)>.179?'#111126':'#ffffff';
  return chip;
}
async function loadTags() {
  tagRegistry = can('view_tags') || hasTagManagement() ? (await api('/tags')).tags : [];
}
function renderTagFilters() {
  $('#tag-filter-panel').hidden = !can('view_tags');
  const list = $('#tag-filters'); list.replaceChildren();
  tagFilters = new Set([...tagFilters].filter(id=>tagRegistry.some(tag=>tag.id===id)));
  for (const tag of tagRegistry) {
    const label=el('label','tag-option'),input=el('input'); input.type='checkbox';input.checked=tagFilters.has(tag.id);
    input.onchange=()=>{input.checked?tagFilters.add(tag.id):tagFilters.delete(tag.id);render();};
    label.append(input,tagChip(tag));list.append(label);
  }
  if (!tagRegistry.length) list.append(el('p','field-hint','Nenhuma tag cadastrada.'));
}
function renderTaskTags() {
  $('#task-tag-section').hidden = !can('view_tags');
  const list=$('#task-tag-options'),query=$('#task-tag-search').value.toLocaleLowerCase('pt-BR');list.replaceChildren();
  for(const tag of tagRegistry.filter(t=>(t.active||selectedTaskTags.has(t.id))&&t.name.toLocaleLowerCase('pt-BR').includes(query))) {
    const label=el('label','tag-option'),input=el('input');input.type='checkbox';input.value=tag.id;input.checked=selectedTaskTags.has(tag.id);
    input.disabled=input.checked?!can('remove_tags'):!can('assign_tags')||!tag.active;
    if(!editing&&!can('create_tasks'))input.disabled=true;
    input.onchange=()=>{
      const checked=input.checked;
      if(!editing){checked?selectedTaskTags.add(tag.id):selectedTaskTags.delete(tag.id);renderTaskTags();return;}
      input.disabled=true;
      queueTaskAction(async()=>{
        const desired=new Set(board.tasks.find(t=>t.id===editing)?.tag_ids||[]);
        checked?desired.add(tag.id):desired.delete(tag.id);
        try {
          await mutate('/tasks/'+editing+'/tags','PATCH',{tag_ids:[...desired]},taskVersion);
          taskVersion=board.version;selectedTaskTags=new Set(board.tasks.find(t=>t.id===editing)?.tag_ids||[]);
          if(can('view_task_history'))await loadHistory(editing,'task-history');
        } finally {renderTaskTags();}
      });
    };
    label.append(input,tagChip(tag));if(!tag.active)label.append(el('small','field-hint','Inativa'));list.append(label);
  }
  if(!list.children.length)list.append(el('p','field-hint','Nenhuma tag disponível.'));
}
function openTag(tag=null) {
  editingTag=tag;cleanDialog($('#tag-dialog'));
  $('#tag-form-title').textContent=tag?'Editar tag':'Nova tag';
  $('#tag-name').value=tag?.name||'';$('#tag-color').value=tag?.color||'#475569';
  $('#tag-description').value=tag?.description||'';$('#tag-active').checked=tag?tag.active:true;
  for(const id of ['tag-name','tag-color','tag-description'])$('#'+id).disabled=tag?!can('edit_tags'):!can('create_tags');
  $('#tag-active').disabled=!can('manage_tag_status');$('#tag-dialog').showModal();
}
function renderTagManagement() {
  const list=$('#tag-list');list.replaceChildren();
  const query=$('#tag-search').value.toLocaleLowerCase('pt-BR'),status=$('#tag-status-filter').value;
  for(const tag of tagRegistry.filter(t=>(t.name+' '+t.description).toLocaleLowerCase('pt-BR').includes(query)&&(!status||t.active===(status==='active')))) {
    const row=el('tr'),name=el('td');name.append(tagChip(tag));row.append(name,el('td','',tag.description),el('td','',tag.active?'Ativa':'Inativa'),el('td','',tag.task_count),el('td','',tag.creator_name+' · '+new Date(tag.created_at).toLocaleString('pt-BR')));
    const actions=el('td','tag-actions');
    if(can('edit_tags')||can('manage_tag_status'))actions.append(button('Editar',()=>openTag(tag),'secondary'));
    if(can('delete_tags'))actions.append(button('Excluir',async()=>{
      if(!confirm('Excluir a tag "'+tag.name+'"? Ela está em '+tag.task_count+' tarefa(s). As tarefas serão mantidas. Para manter as associações, prefira desativar a tag.'))return;
      await api('/tags/'+tag.id,{method:'DELETE',data:{version:tag.version}});await loadTags();renderTagManagement();message('Tag excluída.');
    },'secondary danger'));
    row.append(actions);list.append(row);
  }
  if(!list.children.length){const row=el('tr'),cell=el('td','field-hint','Nenhuma tag encontrada.');cell.colSpan=6;row.append(cell);list.append(row);}
}
async function showTagManagement() {
  if(!hasTagManagement())throw new Error('Você não tem acesso ao cadastro de tags.');
  await loadTags();$('#tag-management').hidden=false;$('#users-list').hidden=true;$('#new-user').hidden=true;
  $('#new-tag').hidden=!can('create_tags');
  renderTagManagement();
}
function hasUpdateAccess() { return ['view_update_status','check_updates','download_updates','install_updates','manage_update_settings','view_update_history'].some(can); }
function hasConfigurationAccess() { return user?.role === 'Administrator' || hasUpdateAccess(); }
async function loadUpdates() {
  $('#updates-panel').hidden = !hasUpdateAccess();
  if (!hasUpdateAccess()) return;
  const readable = can('view_update_status');
  updateInfo = readable ? await api('/updates') : null;
  const release = updateInfo?.state?.release, downloaded = updateInfo?.downloaded;
  $('#update-status').replaceChildren();
  if (updateInfo) {
    const state = updateInfo.state;
    for (const text of ['Versão instalada: ' + updateInfo.current,
      'Última versão: ' + (release?.version || 'Não verificada'),
      'Última consulta: ' + (state.checked_at ? new Date(state.checked_at * 1000).toLocaleString('pt-BR') : 'Nenhuma'),
      state.status === 'available' ? 'Atualização disponível' : state.status === 'current' ? 'Nenhuma versão mais recente' : 'Configure o GitHub e verifique atualizações',
      ...(release?.published_at ? ['Publicação: ' + new Date(release.published_at).toLocaleString('pt-BR')] : []),
      ...(!updateInfo.managed ? ['Para instalar: inicie o servidor com python launcher.py.'] : [])])
      $('#update-status').append(el('p','',text));
    $('#update-owner').value = updateInfo.settings.owner;
    $('#update-repository').value = updateInfo.settings.repository;
    $('#update-channel').value = updateInfo.settings.channel;
    $('#update-token-status').textContent = updateInfo.token_configured ? 'Credencial configurada no servidor (' + updateInfo.token_source + ').' : 'Sem credencial: acesso a repositórios públicos.';
  }
  $('#update-notes').textContent = release?.notes || '';
  for (const [id, permission] of [['check','check_updates'],['download','download_updates'],['install','install_updates'],['rollback','install_updates'],['history-button','view_update_history']])
    $('#update-' + id).hidden = !can(permission) || (['install','rollback'].includes(id) && user.role !== 'Administrator');
  $('#update-settings').hidden = !can('manage_update_settings') || user.role !== 'Administrator';
  $('#update-download').disabled = readable && (!release?.asset_id || updateInfo.state.status !== 'available');
  $('#update-install').disabled = !downloaded || !updateInfo?.managed;
  $('#update-rollback').disabled = !updateInfo?.previous || !updateInfo?.managed;
  if (readable) await showUpdateProgress();
}
async function showUpdateProgress() {
  const {progress} = await api('/updates/progress');
  const labels = {idle:'',downloading:'Baixando pacote…',downloaded:'Pacote validado e pronto para instalar.',installing:'Instalando e verificando o servidor…',installed:'Versão instalada e verificada.',failed:'A operação falhou. Consulte o histórico; a versão anterior foi preservada.'};
  $('#update-progress').hidden = !['downloading','installing'].includes(progress.status);
  $('#update-progress').value = progress.percent;
  $('#update-progress-label').textContent = labels[progress.status] || progress.status;
}
async function watchUpdate() {
  if (updateWatching) return;
  updateWatching = true;
  $('#update-progress-label').textContent = 'Reiniciando o servidor. Aguarde…';
  try {
    for (let attempt=0; attempt<120; attempt++) {
      await new Promise(resolve=>setTimeout(resolve,2000));
      try {
        const response=await fetch('/api/updates/progress',{credentials:'same-origin'});
        if(!response.ok) continue;
        const {progress}=await response.json();
        if(['installed','failed'].includes(progress.status)) {
          location.reload(); return;
        }
      } catch (_) {  }
    }
    message('O servidor ainda não respondeu. Verifique o serviço e o histórico de atualização.',true);
  } finally { updateWatching=false; }
}
const $ = selector => document.querySelector(selector);
let user = null, csrf = '', config = null, board = null, boards = [], people = [], users = [];
let editing = null, taskVersion = null, editingColumn = null, columnVersion = null;
let editingBoard = false, boardVersion = null, editingUser = null;
let dragged = null, pending = false;
let autosaveTimer = null, autosaveBlocked = false, savedTaskFields = '';
let onlyMine = false, activityCursor = null;
let taskActionQueue = Promise.resolve();
let taskDialogEpoch = 0;
let pendingFiles = [], uploadingFiles = false, uploadFailed = false;
let profileData = null, profileFile = null, profileAvatarAction = 'keep', avatarPreviewUrl = null;
let headerIdentityKey = '';
const personName = person => person?.display_name || person?.username || 'Usuário excluído';
function initials(name) {
  const words = name.trim().split(/\s+/u);
  return (words.length > 1 ? Array.from(words[0])[0] + Array.from(words.at(-1))[0] : Array.from(name).slice(0,2).join('')).toUpperCase();
}
function avatar(person, className = '') {
  const name = personName(person), node = el('span', 'user-avatar ' + className, initials(name));
  node.title = name; node.setAttribute('role','img'); node.setAttribute('aria-label',name);
  if (person?.avatar_url) {
    const image = el('img'); image.src = person.avatar_url; image.alt = ''; image.draggable = false;
    image.onerror = () => { node.textContent = initials(name); };
    node.replaceChildren(image);
  }
  return node;
}
function renderIdentity() {
  const key=JSON.stringify([user.id,personName(user),user.role,user.avatar_url]);
  if(key===headerIdentityKey)return;
  headerIdentityKey=key;
  $('#profile-name').textContent = personName(user);
  $('#profile-role').textContent = roleLabel(user.role);
  $('#profile-avatar').replaceChildren(avatar(user));
}
function avatarStack(ids) {
  const group = el('div','avatar-stack');
  const visible = ids.length > 4 ? ids.slice(0,3) : ids;
  for (const id of visible) group.append(avatar(people.find(person => person.id === id)));
  if (ids.length > 4) {
    const extra = el('span','user-avatar avatar-extra','+'+(ids.length-3));
    extra.title = ids.slice(3).map(id=>personName(people.find(person=>person.id===id))).join(', ');
    extra.setAttribute('aria-label',extra.title); group.append(extra);
  }
  return group;
}
function closeProfileMenu() {
  $('#profile-dropdown').hidden = true; $('#profile-menu-toggle').setAttribute('aria-expanded','false');
}
function releaseAvatarPreview() {
  if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
  avatarPreviewUrl = null;
}
function renderProfilePreview() {
  const person = {...profileData,display_name:$('#display-name').value.trim() || profileData.username};
  if (profileAvatarAction === 'remove') person.avatar_url = null;
  if (profileAvatarAction === 'replace') person.avatar_url = avatarPreviewUrl;
  $('#avatar-preview').replaceChildren(avatar(person));
  $('#avatar-remove').disabled = !person.avatar_url;
}
async function openProfile(account = null) {
  closeProfileMenu(); releaseAvatarPreview();
  profileData = (await api(account && account.id !== user.id ? '/users/'+account.id+'/profile' : '/profile')).user;
  profileFile = null; profileAvatarAction = 'keep';
  $('#avatar-file').value = ''; $('#display-name').value = personName(profileData);
  $('#profile-username').value = profileData.username;
  $('#profile-title').textContent = profileData.id === user.id ? 'Meu perfil' : 'Perfil · '+personName(profileData);
  $('#profile-change-password').hidden = profileData.id !== user.id;
  cleanDialog($('#profile-dialog')); renderProfilePreview(); $('#profile-dialog').showModal();
}
let savedTheme = null;
const THEME_DEFAULTS = {navy:'#08073f',yellow:'#e4c204',canvas:'#f5f6fa'};
function applyTheme(colors) {
  for (const [key,value] of Object.entries(colors)) document.documentElement.style.setProperty('--'+key,value);
  const contrast = hex => {
    const linear = [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255).map(c => c <= .04045 ? c/12.92 : ((c+.055)/1.055)**2.4);
    return linear.reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0) > .179 ? '#111126' : '#ffffff';
  };
  document.documentElement.style.setProperty('--on-accent',contrast(colors.yellow));
  document.documentElement.style.setProperty('--on-nav',contrast(colors.navy));
}
function themeFields(colors) {
  for (const key of Object.keys(THEME_DEFAULTS)) $('#theme-'+key).value = colors[key];
}
function previewTheme() {
  applyTheme(Object.fromEntries(Object.keys(THEME_DEFAULTS).map(key => [key,$('#theme-'+key).value])));
  $('#theme-status').textContent = 'Prévia local. Salve para aplicar a todos os usuários.';
}
const ROLE_LABELS = {Administrator: 'Administrador', Manager: 'Gestor', Member: 'Membro', Viewer: 'Leitor'};
const roleLabel = value => ROLE_LABELS[value] || value;
const priorityLabel = value => ({normal: 'Normal', high: 'Alta', low: 'Baixa'}[value] || value);
const displayName = value => ({'My board': 'Meu quadro', 'To do': 'A fazer', 'In progress': 'Em andamento',
  Done: 'Concluído', 'Imported browser board': 'Quadro importado do navegador'}[value] || value);
function activityValues(value, field = '') {
  const labels = {tags:'Tags',task_date:'Data',desired_delivery:'Entrega desejada',title:'Título',description:'Descrição',priority:'Prioridade',column_id:'ID da coluna',column_name:'Coluna',
    position:'Posição',name:'Nome',type:'Tipo',username:'Usuário',user_id:'ID do usuário',role:'Perfil',overrides:'Permissões individuais',
    effective:'Permissões efetivas',enabled:'Conta ativa',filename:'Nome do arquivo',format:'Formato',size:'Tamanho (bytes)',sha256:'SHA-256',
    uploaded_by:'ID de quem enviou',uploaded_at:'Data de envio',uploader:'Enviado por',reason:'Motivo',source:'Origem',
    destination_column_id:'ID da coluna de destino',destination_column_name:'Coluna de destino',
    display_name:'Nome de exibição',has_avatar:'Foto de perfil',avatar_version:'Versão da foto',changed:'Alterada',reset:'Redefinida'};
  if (Array.isArray(value)) return value.map(item => field === 'effective' ? config.permissions[item] || item : activityValues(item));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) =>
    [config.permissions[key] || labels[key] || key, activityValues(item, key)]));
  if (field === 'role') return roleLabel(value);
  if (field === 'priority') return priorityLabel(value);
  if (field === 'type') return {todo:'Início',doing:'Em andamento',done:'Concluído'}[value] || value;
  if (field === 'column_name' || field === 'destination_column_name') return displayName(value);
  if (field === 'reason') return {account_deleted:'Conta excluída',task_deleted:'Tarefa excluída'}[value] || value;
  if (field === 'source' && value === 'browser_import') return 'Importação do navegador';
  if (field === 'uploader' && value === 'Deleted user') return 'Usuário excluído';
  if (field === 'position' && Number.isInteger(value)) return value + 1;
  return typeof value === 'boolean' ? (value ? 'Sim' : 'Não') : value;
}

function el(tag, className = '', text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function message(text, error = false) {
  $('#notice').textContent = text;
  $('#notice').className = 'message' + (error ? ' error' : '');
}
const can = permission => user?.permissions.includes(permission);
function button(text, action, className = 'secondary') {
  const node = el('button', className, text);
  node.type = 'button';
  node.onclick = () => run(action);
  return node;
}
function options(select, entries, value) {
  select.replaceChildren();
  for (const [id, name] of entries) {
    const option = el('option', '', name);
    option.value = id;
    select.append(option);
  }
  if (value !== undefined) select.value = value;
}
async function api(path, {method = 'GET', data, form, version} = {}) {
  const headers = {};
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf;
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  if (version !== undefined) headers['If-Match'] = String(version);
  let response;
  try {
    response = await fetch('/api' + path, {
      method, headers, credentials: 'same-origin',
      body: form || (data !== undefined ? JSON.stringify(data) : undefined),
    });
  } catch {
    throw new Error("Não foi possível conectar ao servidor. Seu formulário continua aberto; verifique a conexão antes de tentar novamente.");
  }
  const result = await response.json().catch(() => ({error: "O servidor retornou uma resposta inesperada."}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') showLogin();
    const error = new Error(result.error || "Não foi possível concluir a operação.");
    error.status = response.status;
    throw error;
  }
  return result;
}
async function run(action) {
  if (pending) return;
  pending = true;
  document.body.setAttribute('aria-busy', 'true');
  try { await action(); }
  catch (error) {
    message(error.message + (error.status === 409 ? " Seu rascunho foi mantido. Feche esta janela e atualize o quadro antes de tentar novamente." : ''), true);
    const dialog = document.querySelector('dialog[open]');
    if (dialog) {
      let feedback = dialog.querySelector('.dialog-feedback');
      if (!feedback) { feedback = el('p', 'dialog-feedback message error'); feedback.setAttribute('role', 'alert'); dialog.prepend(feedback); }
      feedback.textContent = $('#notice').textContent;
    }
  } finally { pending = false; document.body.removeAttribute('aria-busy'); }
}
function cleanDialog(dialog) { dialog.querySelector('.dialog-feedback')?.remove(); }
function showLogin() {
  headerIdentityKey='';
  closeProfileMenu(); releaseAvatarPreview();
  if (savedTheme) applyTheme(savedTheme.colors);
  user = null; csrf = ''; board = null; boards = []; people = []; users = [];
  tagRegistry=[];tagFilters.clear();selectedTaskTags.clear();
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  for (const id of ['workspace', 'admin', 'configuration-screen', 'activity-screen', 'password-screen', 'account']) $('#' + id).hidden = true;
  $('#activity-list').replaceChildren(); $('#task-history').replaceChildren(); $('#standalone-history').replaceChildren();
  $('.board').replaceChildren(); $('#users-list').replaceChildren();
  $('#login-screen').hidden = false;
  $('#login-password').value = '';
}
function showPassword() {
  if (savedTheme) applyTheme(savedTheme.colors);
  $('#workspace').hidden = $('#configuration-screen').hidden = $('#admin').hidden = $('#activity-screen').hidden = true;
  $('#password-screen').hidden = false;
  $('#password-cancel').hidden = user.must_change_password;
  $('#password-hint').textContent = user.must_change_password
    ? "Troque a senha inicial. Mínimo de 10 caracteres."
    : "Mínimo de 10 caracteres. Os outros acessos serão encerrados.";
  $('#password-form').reset();
}
async function signedIn(result) {
  user = result.user; csrf = result.csrf;
  $('#login-screen').hidden = true; $('#account').hidden = false;
  renderIdentity();
  $('#board-menu').hidden = !can('view_boards') || user.must_change_password;
  $('#settings').hidden = !hasAdminAccess() || user.must_change_password;
  $('#configuration-menu').hidden = !hasConfigurationAccess() || user.must_change_password;
  $('#activity-menu').hidden = !can('view_activity_log') || user.must_change_password;
  if (user.must_change_password) { showPassword(); return; }
  $('#password-screen').hidden = true;
  config = await api('/config');
  $('#files').removeAttribute('accept');
  if (can('view_boards')) await loadWorkspace();
  else {
    $('#workspace').hidden = $('#configuration-screen').hidden = $('#admin').hidden = true;
    if (hasAdminAccess()) await showAdmin();
    else if (hasConfigurationAccess()) await showConfiguration();
    else if (can('view_activity_log')) await showActivity();
    else message("Sua conta não tem acesso aos quadros. Solicite a liberação a um administrador.");
  }
}
async function loadWorkspace(preferred) {
  savedTheme = (await api('/theme')).theme; applyTheme(savedTheme.colors);
  if (!can('view_boards')) return;
  const list = await api('/boards');
  const directory = await api('/people');
  boards = list.boards; people = directory.users;
  await loadTags();renderTagFilters();
  const id = boards.find(b => b.id === (preferred || board?.id))?.id || boards[0]?.id;
  board = id ? (await api('/boards/' + id)).board : null;
  $('#workspace').hidden = false; $('#admin').hidden = $('#configuration-screen').hidden = $('#password-screen').hidden = $('#activity-screen').hidden = true;
  $('#new').hidden = !can('create_tasks');
  $('#new-column').hidden = !can('manage_columns');
  for (const name of ['new-board', 'rename-board', 'delete-board']) $('#' + name).hidden = !can('manage_boards');
  $('#import-box').hidden = !can('manage_boards');
  $('#import-browser').hidden = !legacyBoard();
  const selectedAssignee = $('#assignee-filter').value;
  options($('#assignee-filter'), [['', "Todos os responsáveis"], ...people.map(p => [p.id, personName(p)])]);
  $('#assignee-filter').value = people.some(p => p.id === selectedAssignee) ? selectedAssignee : '';
  $('#assignee-filter').hidden = !can('view_task_assignees');
  render();
}
function render() {
  options($('#board-select'), boards.map(b => [b.id, displayName(b.name)]), board?.id);
  const surface = $('.board'); surface.replaceChildren();
  if (!board) { surface.append(el('p', 'muted', "Nenhum quadro disponível.")); return; }
  $('#delete-board').disabled = boards.length <= 1;
  const done = new Set(board.columns.filter(c => c.type === 'done').map(c => c.id));
  const completed = board.tasks.filter(t => done.has(t.column_id)).length;
  $('#summary').textContent = board.tasks.length + (board.tasks.length === 1 ? ' tarefa' : ' tarefas') +
    ' · ' + completed + (completed === 1 ? ' concluída' : ' concluídas');
  const query = $('#search').value.toLowerCase();
  const selectedAssignee = can('view_task_assignees') ? $('#assignee-filter').value : '';
  for (const [index, column] of board.columns.entries()) {
    const col = el('section', 'column'); col.dataset.type = column.type;
    col.setAttribute('aria-label', displayName(column.name));
    const list = board.tasks.filter(t => t.column_id === column.id &&
      (t.title + ' ' + t.description + ' ' + (t.tags || []).join(' ')).toLowerCase().includes(query) &&
      (!onlyMine || t.assigned_to_me) && (!selectedAssignee || t.assignees.includes(selectedAssignee)) &&
      (!can('view_tags') || [...tagFilters].every(id=>(t.tag_ids||[]).includes(id))));
    const head = el('div', 'column-head'), heading = el('span', '', displayName(column.name));
    heading.append(el('span', 'type-label', {todo: "Início", doing: "Em andamento", done: "Concluído"}[column.type]));
    head.append(el('span', 'dot'), heading, el('span', 'count', list.length));
    if (can('manage_columns')) {
      for (const [label, offset] of [['←', -1], ['→', 1]]) {
        const arrow = button(label, () => mutate('/columns/' + column.id + '/move', 'POST', {position: index + offset}), 'col-order');
        arrow.disabled = index + offset < 0 || index + offset >= board.columns.length;
        arrow.setAttribute('aria-label', "Mover " + column.name + (offset < 0 ? " para a esquerda" : " para a direita"));
        head.append(arrow);
      }
    }
    if (can('manage_columns') || can('delete_columns')) {
      const settings = button('⚙', () => openColumn(column), '');
      settings.setAttribute('aria-label', "Configurações de " + column.name); head.append(settings);
    }
    col.append(head);
    if (can('move_tasks')) {
      col.ondragover = event => { if (dragged) {event.preventDefault(); col.classList.add('over');} };
      col.ondragleave = event => { if (!col.contains(event.relatedTarget)) col.classList.remove('over'); };
      col.ondrop = event => {
        event.preventDefault(); col.classList.remove('over');
        const taskId = dragged; dragged = null;
        if (taskId) run(() => moveTask(taskId, column.id));
      };
    }
    for (const task of list) {
      const card = el('article', 'card'); card.draggable = can('move_tasks');
      card.ondragstart = event => {
        dragged = task.id; event.dataTransfer.setData('text/plain', task.id); event.dataTransfer.effectAllowed = 'move';
      };
      card.ondragend = () => { dragged = null; document.querySelectorAll('.over').forEach(c => c.classList.remove('over')); };
      if (can('move_tasks')) {
        card.ondragover = event => { if (dragged && dragged !== task.id) event.preventDefault(); };
        card.ondrop = event => {
          event.preventDefault(); event.stopPropagation(); col.classList.remove('over');
          const taskId = dragged; dragged = null;
          if (taskId && taskId !== task.id) run(() => moveTask(taskId, column.id, task.id));
        };
      }
      const title = button(task.title, () => openTask(task), 'icon task-title');
      
      const h3 = el('h3'); h3.append(title); card.append(h3);
      if (task.description) card.append(el('p', '', task.description));
      if(task.tags?.length) {
        const tags=el('div','task-tags');
        for(const tag of task.tag_details || []) tags.append(tagChip(tag));
        card.append(tags);
      }
      for (const [field,label] of [['task_date','Data'],['desired_delivery','Entrega desejada']]) {
        if(task[field]) card.append(el('div','task-date',label+': '+task[field].split('-').reverse().join('/')));
      }
      if (can('view_task_assignees') && task.assignees.length) {
        const identities = el('div','card-identities'); identities.append(avatarStack(task.assignees)); card.append(identities);
      }
      if (task.attachments.length) card.append(el('div', 'attachment-count', task.attachments.length + " anexo(s)"));
      const foot = el('div', 'card-foot');
      if (can('move_tasks')) {
        const move = el('select'); options(move, board.columns.map(c => [c.id, displayName(c.name)]), task.column_id);
        move.setAttribute('aria-label', "Mover " + task.title);
        move.onchange = () => run(() => moveTask(task.id, move.value)); foot.append(move);
      }
      foot.append(button(can('edit_tasks') ? "Detalhes / Editar" : "Detalhes", () => openTask(task), 'icon edit'));
      if (can('delete_tasks')) foot.append(button("Excluir", async () => {
        if (confirm("Excluir “" + task.title + "” e todos os seus arquivos anexados?")) await mutate('/tasks/' + task.id, 'DELETE');
      }, 'icon'));
      card.append(foot); col.append(card);
    }
    if (!list.length) col.append(el('p', 'empty', query ? "Nenhuma tarefa encontrada." : "Sem tarefas."));
    if (can('create_tasks')) col.append(button("+ Adicionar tarefa", () => openTask(null, column.id), 'add'));
    surface.append(col);
  }
}
async function mutate(path, method, data = {}, version = board.version) {
  const result = await api(path, {method, data: {...data, version}});
  if (result.board) {
    board = result.board;
    boards = boards.map(b => b.id === board.id ? {...b, name: board.name, version: board.version} : b);
    render();
  }
  message("Alterações salvas.");
  return result;
}
async function moveTask(taskId, columnId, beforeId = null) {
  await mutate('/tasks/' + taskId + '/move', 'POST', {column_id: columnId, before_id: beforeId});
}
async function openTask(task = null, status = board.columns.find(c => c.type === 'todo')?.id || board.columns[0].id, preserveFiles = false) {
  taskDialogEpoch++;
  if (!preserveFiles) { pendingFiles = []; uploadFailed = false; }
  clearTimeout(autosaveTimer); autosaveBlocked = false;
  editing = task?.id || null; taskVersion = board.version;
  cleanDialog($('#dialog'));
  $('#form-title').textContent = task ? "Detalhes da tarefa" : "Nova tarefa";
  $('#title').value = task?.title || ''; $('#description').value = task?.description || '';
  const today = new Date();
  $('#task-date').value = task ? task.task_date || '' : [today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
  $('#desired-delivery').value = task?.desired_delivery || '';
  selectedTaskTags = new Set(task?.tag_ids || []);$('#task-tag-search').value='';renderTaskTags();
  options($('#status'), board.columns.map(c => [c.id, displayName(c.name)]), task?.column_id || status);
  $('#status').disabled = !!task || !can('create_tasks');
  syncAssignments(task);
  const editable = task ? can('edit_tasks') : can('create_tasks');
  for (const id of ['title', 'description', 'task-date', 'desired-delivery']) $('#' + id).disabled = !editable;
  $('#save-task').hidden = !editable;
  savedTaskFields = JSON.stringify(taskFields());
  $('#files').value = ''; renderPendingFiles();
  renderAttachments(task); $('#dialog').showModal();
  $('#task-history-section').hidden = !can('view_task_history');
  $('#task-history').replaceChildren(); $('#task-history-more').hidden = true;
  if (task && can('view_task_history')) await loadHistory(task.id, 'task-history');
  else if (can('view_task_history')) $('#task-history').append(el('p', 'field-hint', "Salve a tarefa para iniciar seu histórico."));
}
function taskFields() {
  const data = {title: $('#title').value.trim(), description: $('#description').value, priority: board?.tasks.find(task => task.id === editing)?.priority || 'normal', task_date: $('#task-date').value || null, desired_delivery: $('#desired-delivery').value || null};
  if(!editing && can('view_tags'))data.tag_ids=[...selectedTaskTags];
  if (!editing && (can('assign_users') || can('assign_self'))) {
    data.assignees = can('view_task_assignees')
      ? [...$('#assignees').selectedOptions].map(option => option.value)
      : $('#assign-self').checked ? [user.id] : [];
  }
  return data;
}
async function saveTaskDraft() {
  const data = taskFields();
  if (!data.title) throw new Error("Informe o nome da tarefa antes de salvar.");
  if (JSON.stringify(data) === savedTaskFields) return;
  await mutate('/tasks/' + editing, 'PATCH', data, taskVersion);
  taskVersion = board.version; savedTaskFields = JSON.stringify(data);
  if (can('view_task_history')) await loadHistory(editing, 'task-history');
}
function syncAssignments(task) {
  $('#assignee-section').hidden = !can('view_task_assignees');
  options($('#assignees'), people.map(p => [p.id, personName(p) + (p.enabled ? '' : " (desativado)")]));
  for (const option of $('#assignees').options) {
    option.selected = task?.assignees.includes(option.value) || false;
    option.disabled = !can('assign_users') && option.value !== user.id;
  }
  $('#assignees').disabled = !can('assign_users') && !can('assign_self');
  $('#assigned-people').replaceChildren();
  for (const id of task?.assignees || []) {
    const person = people.find(p=>p.id===id), chip = el('span','assigned-person');
    chip.append(avatar(person),el('span','',personName(person))); $('#assigned-people').append(chip);
  }
  renderAssigneeOptions();
  $('#assignment-hint').textContent = can('assign_users') ? "Ctrl / Command para selecionar mais de um responsável."
    : can('assign_self') ? "Permissão para atribuir apenas a si mesmo." : "Responsáveis: somente leitura.";
  $('#self-assignment').hidden = can('view_task_assignees') || (!can('assign_self') && !can('assign_users'));
  $('#assign-self').checked = !!task?.assigned_to_me;
  $('#assignment-controls').hidden = !task || !can('assign_users') || can('view_task_assignees');
  options($('#assignment-person'), people.map(p => [p.id, personName(p)]));
}
function renderAssigneeOptions() {
  const list = $('#assignee-options'); list.replaceChildren();
  for (const option of $('#assignees').options) {
    const person = people.find(p=>p.id===option.value), label = el('label','assignee-option'), checkbox = el('input');
    checkbox.type = 'checkbox'; checkbox.checked = option.selected;
    checkbox.disabled = $('#assignees').disabled || option.disabled;
    checkbox.dataset.userId = option.value;
    checkbox.onchange = () => {
      option.selected = checkbox.checked;
      $('#assignees').dispatchEvent(new Event('change',{bubbles:true}));
    };
    label.append(checkbox,avatar(person),el('span','',option.textContent)); list.append(label);
  }
}
async function changeAssignments(add, remove) {
  if (!editing) return;
  await mutate('/tasks/' + editing + '/assignees', 'PATCH', {add, remove}, taskVersion);
  taskVersion = board.version;
  syncAssignments(board.tasks.find(t => t.id === editing));
  if (can('view_task_history')) await loadHistory(editing, 'task-history');
}
function queueTaskAction(action) {
  const taskId = editing, actorId = user?.id, epoch = taskDialogEpoch;
  taskActionQueue = taskActionQueue.then(async () => {
    while (pending) await new Promise(resolve => setTimeout(resolve, 40));
    if (editing !== taskId || user?.id !== actorId || taskDialogEpoch !== epoch || !$('#dialog').open) return;
    await run(action);
  });
}
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  if (!editing || !can('edit_tasks') || autosaveBlocked) return;
  autosaveTimer = setTimeout(() => {
    if (!$('#dialog').open) return;
    if (pending) { scheduleAutosave(); return; }
    if (!$('#title').value.trim()) return;
    run(async () => {
      try { await saveTaskDraft(); }
      catch (error) { autosaveBlocked = true; throw error; }
    });
  }, 900);
}
function renderPendingFiles() {
  const list = $('#queued-files'); list.replaceChildren();
  $('#files').disabled = uploadingFiles;
  $('#files-status').textContent = uploadingFiles ? 'Enviando arquivos…' : pendingFiles.length ? pendingFiles.length + ' arquivo(s) na seleção' : 'Nenhum arquivo selecionado';
  $('#retry-upload').hidden = !editing || !uploadFailed || !pendingFiles.length;
  for (const file of pendingFiles) {
    const row = el('div', 'attachment');
    row.append(el('span', '', file.name), el('small', '', !editing ? 'Será enviado ao salvar a tarefa.' : 'Aguardando envio.'));
    const remove = button('Remover da seleção', () => {
      pendingFiles = pendingFiles.filter(item => item !== file);
      renderPendingFiles();
    }, 'icon');
    remove.disabled = uploadingFiles;
    row.append(remove); list.append(row);
  }
}
async function uploadPendingFiles() {
  if (!editing || !pendingFiles.length || uploadingFiles) return;
  clearTimeout(autosaveTimer);
  uploadingFiles = true; uploadFailed = false; renderPendingFiles();
  let count = 0;
  try {
    for (const file of [...pendingFiles]) {
      const form = new FormData(); form.append('file', file);
      const result = await api('/tasks/' + editing + '/attachments', {method:'POST', form, version:taskVersion});
      board = result.board; taskVersion = board.version;
      pendingFiles = pendingFiles.filter(item => item !== file);
      count++; render(); renderAttachments();
    }
    message('Arquivos enviados.');
  } catch (error) {
    uploadFailed = pendingFiles.length > 0;
    error.message = (count ? count + ' arquivo(s) enviado(s). ' : '') + 'Não foi possível concluir o envio. Os arquivos restantes continuam na seleção. ' + error.message;
    throw error;
  } finally {
    uploadingFiles = false; renderPendingFiles(); scheduleAutosave();
  }
  if (can('view_task_history')) await loadHistory(editing, 'task-history');
}
function renderAttachments(task = board.tasks.find(t => t.id === editing)) {
  const list = $('#attachments'); list.replaceChildren();
  $('#attachment-hint').textContent = !task ? "Os arquivos selecionados serão enviados ao salvar a tarefa."
    : "Todos os formatos. Até " + (config.max_file_bytes / 1024 / 1024) + " MB por arquivo.";
  $('#upload-form').hidden = !can('upload_attachments');
  renderPendingFiles();
  for (const attachment of task?.attachments || []) {
    const item = el('div', 'attachment'), link = el('a', '', attachment.filename);
    link.href = '/api/attachments/' + attachment.id + '/download';
    item.append(link, el('small', '', attachment.format.toUpperCase() + ' · ' +
      new Intl.NumberFormat('pt-BR').format(attachment.size) + ' bytes · ' + new Date(attachment.uploaded_at).toLocaleString('pt-BR') +
      ' · ' + (attachment.uploader || "Usuário excluído")));
    if (can('delete_attachments') || (can('delete_own_attachments') && attachment.uploaded_by === user.id)) item.append(button("Excluir arquivo", async () => {
      if (!confirm("Excluir “" + attachment.filename + '”?')) return;
      await mutate('/attachments/' + attachment.id, 'DELETE', {}, taskVersion);
      taskVersion = board.version; renderAttachments();
      if (can('view_task_history')) await loadHistory(editing, 'task-history');
    }, 'icon'));
    list.append(item);
  }
  if (task && !task.attachments.length) list.append(el('p', 'field-hint', can('download_attachments') ? "Nenhum arquivo anexado." : "Você não tem permissão para visualizar ou baixar arquivos."));
}
function openColumn(column = null) {
  editingColumn = column?.id || null; columnVersion = board.version;
  cleanDialog($('#column-dialog'));
  $('#column-form-title').textContent = column ? "Configurações da coluna" : "Nova coluna";
  $('#column-name').value = column?.name || ''; $('#column-type').value = column?.type || 'todo';
  $('#column-name').disabled = $('#column-type').disabled = !can('manage_columns');
  $('#column-submit').hidden = !can('manage_columns');
  $('#column-removal').hidden = !column || !can('delete_columns');
  options($('#move-tasks'), board.columns.filter(c => c.id !== column?.id).map(c => [c.id, displayName(c.name)]));
  $('#remove-column').disabled = board.columns.length <= 1;
  $('#remove-help').textContent = board.columns.length <= 1 ? "Mantenha pelo menos uma coluna." : "As tarefas e seus arquivos serão movidos para a coluna selecionada.";
  $('#column-dialog').showModal();
}
function openBoard(rename = false) {
  editingBoard = rename; boardVersion = board?.version;
  cleanDialog($('#board-dialog'));
  $('#board-form-title').textContent = rename ? "Renomear quadro" : "Novo quadro";
  $('#board-name').value = rename ? board.name : ''; $('#board-dialog').showModal();
}
async function showAdmin() {
  if (!hasAdminAccess()) throw new Error('Você não tem acesso à administração.');
  if (savedTheme) applyTheme(savedTheme.colors);
  const canUsers=can('manage_users')||can('manage_permissions');
  users = canUsers ? (await api('/users')).users : [];
  $('#workspace').hidden = $('#configuration-screen').hidden = $('#password-screen').hidden = $('#activity-screen').hidden = true; $('#admin').hidden = false;
  $('#back-board').hidden = !can('view_boards');
  $('#new-user').hidden = !can('manage_users');
  $('#admin-users-tab').hidden=!canUsers;$('#admin-tags-tab').hidden=!hasTagManagement();
  $('#tag-management').hidden=true;$('#users-list').hidden=false;
  if(!canUsers){await showTagManagement();return;}
  const list = $('#users-list'); list.replaceChildren();
  for (const account of users) {
    const row = el('div', 'user-row'), text = el('div');
    const identity = el('div','admin-user-identity'); identity.append(avatar(account));
    text.append(el('strong', '', personName(account)), el('div','field-hint','Login: '+account.username), el('div', 'field-hint', roleLabel(account.role) + ' · ' +
      (account.enabled ? "Ativa" : "Desativada") + (account.must_change_password ? " · Alteração de senha pendente" : '')));
    identity.append(text); row.append(identity, button(account.id === user.id ? "Ver conta" : "Gerenciar", () => openUser(account)));
    list.append(row);
  }
}
async function showConfiguration() {
  if (!hasConfigurationAccess()) throw new Error('Você não tem acesso às configurações.');
  savedTheme = (await api('/theme')).theme;
  applyTheme(savedTheme.colors); themeFields(savedTheme.colors);
  $('#appearance').hidden = user.role !== 'Administrator';
  $('#theme-status').textContent = '';
  await loadUpdates();
  $('#workspace').hidden = $('#admin').hidden = $('#password-screen').hidden = $('#activity-screen').hidden = true;
  $('#configuration-screen').hidden = false;
  $('#configuration-back').hidden = !can('view_boards');
}
function drawPermissions(role, overrides = {}) {
  const list = $('#permissions'); list.replaceChildren();
  const groups = new Map();
  const category = key => key.includes('update') ? 'Atualizações' : ['manage_users','manage_permissions'].includes(key) ? 'Administração'
    : key.includes('tag') ? 'Tags' : key.includes('attachments') ? 'Arquivos' : ['view_activity_log','view_task_history'].includes(key) ? 'Histórico e atividades'
    : key.includes('boards') || key.includes('columns') ? 'Quadros e colunas' : 'Tarefas e responsáveis';
  for (const [key, description] of Object.entries(config.permissions)) {
    const group = category(key);
    if (!groups.has(group)) {
      const fieldset = el('fieldset'); fieldset.append(el('legend','',group)); groups.set(group, fieldset); list.append(fieldset);
    }
    const label = el('label'), input = el('input'); input.type = 'checkbox'; input.dataset.permission = key;
    input.checked = role === 'Administrator' || (overrides[key] ?? config.roles[role].includes(key));
    input.disabled = !can('manage_permissions') || role === 'Administrator' || editingUser?.id === user.id;
    label.append(input, document.createTextNode(description)); groups.get(group).append(label);
  }
}
function openUser(account = null) {
  editingUser = account; const self = account?.id === user.id;
  cleanDialog($('#user-dialog')); $('#user-form').reset();
  $('#user-form-title').textContent = account ? "Conta · " + account.username : "Nova conta";
  $('#user-name').value = account?.username || ''; $('#user-name').disabled = self || !can('manage_users');
  options($('#user-role'), Object.keys(config.roles).map(r => [r, roleLabel(r)]), account?.role || 'Member');
  $('#user-role').disabled = self || !can('manage_permissions');
  $('#user-enabled').value = String(account?.enabled ?? true); $('#user-enabled').disabled = self || !can('manage_users');
  $('#temporary-password').value = ''; $('#temporary-password').required = !account;
  $('#temporary-password').disabled = self || !can('manage_users');
  $('#temporary-hint').textContent = account ? "Informe uma senha temporária e clique em Redefinir senha. O usuário deverá trocá-la no próximo acesso." : "O usuário deverá alterar esta senha no primeiro acesso.";
  $('#save-user').hidden = self;
  $('#reset-user-password').hidden = $('#delete-user').hidden = !account || self || !can('manage_users');
  $('#edit-user-profile').hidden = !account || user.role !== 'Administrator';
  $('#role-defaults').disabled = self || !can('manage_permissions');
  drawPermissions(account?.role || 'Member', account?.overrides || {});
  $('#user-dialog').showModal();
}
function activityText(entry) {
  const name = '“' + (entry.entity_display_name || entry.entity_name) + '”', before = entry.previous, after = entry.new;
  const actorName = entry.actor_display_name || entry.actor_name;
  const verbs = {
    'tag.created':'cadastrou a tag', 'tag.updated':'alterou a tag', 'tag.deleted':'excluiu a tag',
    'tag.activated':'ativou a tag', 'tag.deactivated':'desativou a tag',
    'update.configured':'configurou as atualizações de', 'update.checked':'verificou atualizações de',
    'update.downloaded':'baixou uma atualização de', 'update.install_requested':'autorizou a atualização de',
    'update.rollback_requested':'autorizou a restauração de', 'update.installed':'atualizou',
    'update.failed':'registrou uma falha na atualização de', 'update.rolled_back':'restaurou a versão anterior de',
    'settings.theme_changed': 'alterou as cores do sistema em',
    'user.profile_updated':'alterou o nome de exibição de', 'user.avatar_changed':'alterou a foto de',
    'user.password_changed':'alterou a senha de', 'user.password_reset':'redefiniu a senha de',
    'task.created': "criou a tarefa", 'task.updated': "editou a tarefa", 'task.deleted': "excluiu a tarefa",
    'column.created': "criou a coluna", 'column.updated': "renomeou ou alterou a coluna",
    'column.deleted': "excluiu a coluna", 'column.reordered': "reordenou a coluna",
    'attachment.uploaded': "enviou o arquivo", 'attachment.deleted': "excluiu o arquivo",
    'user.created': "criou a conta", 'user.updated': "alterou a conta", 'user.deleted': "excluiu a conta",
    'user.role_changed': "alterou o perfil de", 'user.permissions_changed': "alterou as permissões de",
    'board.created': "criou o quadro", 'board.updated': "alterou o quadro", 'board.deleted': "excluiu o quadro",
  };
  if (entry.action === 'task.moved') return actorName + " moveu a tarefa " + name +
    " de “" + displayName(before.column_name) + "” (posição " + (before.position + 1) + ") para “" + displayName(after.column_name) + "” (posição " + (after.position + 1) + ').';
  if (entry.action === 'task.assigned') return actorName + " atribuiu o usuário “" + (after.display_name || after.username) + "” à tarefa " + name + '.';
  if (entry.action === 'task.unassigned') return actorName + " removeu o usuário “" + (before.display_name || before.username) + "” da tarefa " + name + '.';
  if (entry.action === 'task.tag_added') return actorName + ' adicionou a tag “' + after.name + '” à tarefa ' + name + '.';
  if (entry.action === 'task.tag_removed') return actorName + ' removeu a tag “' + before.name + '” da tarefa ' + name + '.';
  return actorName + ' ' + (verbs[entry.action] || entry.action) + ' ' + name + '.';
}
function appendActivity(container, entries, allowHistory = false) {
  for (const entry of entries) {
    const row = el('article', 'log-entry'), stamp = el('time');
    stamp.dateTime = entry.occurred_at;
    stamp.title = entry.occurred_at;
    stamp.textContent = new Intl.DateTimeFormat('pt-BR', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      second: '2-digit', fractionalSecondDigits: 3, timeZoneName: 'short',
    }).format(new Date(entry.occurred_at));
    row.append(stamp, el('p', '', activityText(entry)));
    if (entry.board_name) row.append(el('small', 'muted', "Quadro: " + displayName(entry.board_name)));
    const details = el('details'), summary = el('summary', '', "O que mudou");
    details.append(summary, el('pre', '', JSON.stringify({Antes: activityValues(entry.previous), Depois: activityValues(entry.new)}, null, 2)));
    row.append(details);
    if (allowHistory && entry.task_id && can('view_task_history')) row.append(button("Ver histórico da tarefa", async () => {
      $('#history-title').textContent = "Histórico · " + entry.task_name;
      cleanDialog($('#history-dialog')); $('#history-dialog').showModal();
      await loadHistory(entry.task_id, 'standalone-history');
    }, 'icon'));
    container.append(row);
  }
}
async function loadHistory(taskId, target, append = false) {
  const container = $('#' + target), more = $('#' + target + '-more');
  if (!can('view_task_history')) { container.replaceChildren(); more.hidden = true; return; }
  if (!append) { container.replaceChildren(); container.dataset.taskId = taskId; delete more.dataset.cursor; }
  const cursor = append ? more.dataset.cursor : null;
  const result = await api('/tasks/' + encodeURIComponent(taskId) + '/history' + (cursor ? '?before=' + cursor : ''));
  if (container.dataset.taskId !== taskId) return;
  appendActivity(container, result.entries);
  if (!container.children.length) container.append(el('p', 'field-hint', "Nenhuma atividade registrada."));
  more.hidden = !result.next_cursor;
  more.dataset.cursor = result.next_cursor || '';
  more.onclick = () => run(() => loadHistory(taskId, target, true));
}
async function showActivity() {
  if (savedTheme) applyTheme(savedTheme.colors);
  const filters = await api('/activity/options');
  $('#workspace').hidden = $('#configuration-screen').hidden = $('#admin').hidden = $('#password-screen').hidden = true;
  $('#activity-screen').hidden = false;
  options($('#activity-user'), [['', "Todos os usuários"], ...filters.actors.map(a => [a.id, a.name])], $('#activity-user').value || '');
  options($('#activity-task'), [['', "Todas as tarefas"], ...filters.tasks.map(t => [t.id, t.name + ' · ' + t.id.slice(0, 8)])], $('#activity-task').value || '');
  options($('#activity-action'), [['', "Todas as ações"], ['task', "Ações em tarefas"], ['column', "Ações em colunas"],
    ['attachment', "Ações em arquivos"], ['user', "Ações em contas"], ['board', "Ações em quadros"],
    ...Object.entries(config.activity_actions)], $('#activity-action').value || '');
  await loadActivity();
}
async function loadActivity(append = false) {
  const params = new URLSearchParams();
  for (const [id, key] of [['activity-user', 'actor_id'], ['activity-task', 'task_id'], ['activity-action', 'action']]) {
    if ($('#' + id).value) params.set(key, $('#' + id).value);
  }
  if ($('#activity-start').value) params.set('start', new Date($('#activity-start').value + 'T00:00:00').toISOString());
  if ($('#activity-end').value) {
    const end = new Date($('#activity-end').value + 'T00:00:00'); end.setDate(end.getDate() + 1);
    params.set('end', end.toISOString());
  }
  if (append && activityCursor) params.set('before', activityCursor);
  const result = await api('/activity?' + params);
  if (!append) $('#activity-list').replaceChildren();
  appendActivity($('#activity-list'), result.entries, true);
  if (!$('#activity-list').children.length) $('#activity-list').append(el('p', 'muted', "Nenhuma atividade encontrada para estes filtros."));
  activityCursor = result.next_cursor; $('#activity-more').hidden = !activityCursor;
}
function legacyBoard() {
  try {
    const snapshot = JSON.parse(localStorage.getItem('everyday-kanban-board-v2') || 'null');
    if (snapshot) return snapshot;
    const tasks = JSON.parse(localStorage.getItem('everyday-kanban-v1') || 'null');
    const columns = [['todo', 'To do', 'todo'], ['doing', "Em andamento", 'doing'], ['done', "Concluído", 'done']];
    const extra = JSON.parse(localStorage.getItem('everyday-kanban-columns-v1') || '[]');
    if (tasks || extra.length) return {columns: columns.concat(extra.map(c => [c[0], c[1], 'todo'])), tasks: tasks || []};
  } catch {  }
  return null;
}
async function importBoard(payload) {
  const result = await api('/import', {method: 'POST', data: {board: payload}});
  await loadWorkspace(result.board_id);
  message(result.already_imported ? "Este quadro já foi importado." : "Quadro importado. Os dados originais do navegador foram mantidos.");
}

$('#login-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const result = await api('/login', {method: 'POST', data: {username: $('#username').value.trim(), password: $('#login-password').value}});
  $('#login-password').value = ''; message(''); await signedIn(result);
}); };
$('#logout').onclick = () => run(async () => { closeProfileMenu(); await api('/logout', {method: 'POST', data: {}}); showLogin(); message("Você saiu da sua conta."); });
$('#profile-menu-toggle').onclick = () => {
  const open = $('#profile-dropdown').hidden;
  $('#profile-dropdown').hidden = !open; $('#profile-menu-toggle').setAttribute('aria-expanded',String(open));
};
document.addEventListener('click',event=>{ if(!event.target.closest('.profile-chip')) closeProfileMenu(); });
document.addEventListener('keydown',event=>{ if(event.key==='Escape') closeProfileMenu(); });
$('#my-profile').onclick = () => run(()=>openProfile());
$('#profile-close').onclick = () => $('#profile-dialog').close();
$('#profile-dialog').addEventListener('close',releaseAvatarPreview);
$('#display-name').oninput = renderProfilePreview;
$('#avatar-file').onchange = () => run(async()=>{
  const file = $('#avatar-file').files[0]; if(!file) return;
  if(file.size > config.max_avatar_bytes) throw new Error('A foto deve ter no máximo 5 MB.');
  if(!/\.(jpe?g|png|webp)$/i.test(file.name)) throw new Error('Use uma foto JPG, PNG ou WebP.');
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src=url; await image.decode();
    if(image.naturalWidth*image.naturalHeight>16000000) throw new Error('A foto deve ter no máximo 16 megapixels.');
  } catch(error) { URL.revokeObjectURL(url); throw new Error(error.message.includes('megapixels')?error.message:'Não foi possível ler a imagem.'); }
  releaseAvatarPreview(); avatarPreviewUrl=url; profileFile=file; profileAvatarAction='replace'; renderProfilePreview();
});
$('#avatar-remove').onclick = () => {
  releaseAvatarPreview(); profileFile=null; profileAvatarAction='remove'; $('#avatar-file').value=''; renderProfilePreview();
};
$('#profile-form').onsubmit = event => { event.preventDefault(); run(async()=>{
  const form = new FormData(); form.set('display_name',$('#display-name').value.trim());
  form.set('version',profileData.profile_version); form.set('avatar_action',profileAvatarAction);
  if(profileAvatarAction==='replace') form.set('avatar',profileFile);
  const self=profileData.id===user.id;
  const result = await api(self?'/profile':'/users/'+profileData.id+'/profile',{method:'PATCH',form});
  const updated=result.user;
  if(self){user=updated;renderIdentity();}
  people=people.map(person=>person.id===updated.id?{...person,...updated}:person);
  users=users.map(person=>person.id===updated.id?{...person,...updated}:person);
  $('#profile-dialog').close();
  if(board)render();
  if(!$('#admin').hidden) await showAdmin();
  message('Perfil salvo.');
}); };
$('#profile-change-password').onclick = () => { $('#profile-dialog').close(); showPassword(); };
$('#edit-user-profile').onclick = () => run(async()=>{ const account=editingUser;$('#user-dialog').close();await openProfile(account); });
$('#password-cancel').onclick = () => run(() => loadWorkspace());
$('#password-form').onsubmit = event => { event.preventDefault(); run(async () => {
  if ($('#new-password').value !== $('#confirm-password').value) throw new Error("As novas senhas não coincidem.");
  const result = await api('/password', {method: 'POST', data: {current_password: $('#current-password').value,
    new_password: $('#new-password').value, confirm_password: $('#confirm-password').value}});
  $('#password-form').reset(); await signedIn(result); message("Sua senha foi alterada.");
}); };
$('#board-menu').onclick = () => run(() => loadWorkspace());
$('#settings').onclick = () => run(showAdmin);
$('#admin-users-tab').onclick=()=>run(showAdmin);
$('#admin-tags-tab').onclick=()=>run(showTagManagement);
$('#new-tag').onclick=()=>openTag();
$('#tag-cancel').onclick=()=>$('#tag-dialog').close();
$('#tag-search').oninput=renderTagManagement;
$('#tag-status-filter').onchange=renderTagManagement;
$('#task-tag-search').oninput=renderTaskTags;
$('#clear-tag-filters').onclick=()=>{tagFilters.clear();renderTagFilters();render();};
$('#tag-form').onsubmit=event=>{event.preventDefault();run(async()=>{
  const data={name:$('#tag-name').value.trim(),color:$('#tag-color').value,description:$('#tag-description').value,active:$('#tag-active').checked};
  if(editingTag)data.version=editingTag.version;
  await api('/tags'+(editingTag?'/'+editingTag.id:''),{method:editingTag?'PATCH':'POST',data});
  $('#tag-dialog').close();await loadTags();renderTagManagement();message('Tag salva.');
});};
$('#configuration-menu').onclick = () => run(showConfiguration);
$('#configuration-back').onclick = () => run(() => loadWorkspace());
for (const key of Object.keys(THEME_DEFAULTS)) $('#theme-'+key).oninput = previewTheme;
$('#theme-reset').onclick = () => { themeFields(THEME_DEFAULTS); previewTheme(); };
$('#theme-cancel').onclick = () => { themeFields(savedTheme.colors); applyTheme(savedTheme.colors); $('#theme-status').textContent=''; };
$('#theme-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const colors = Object.fromEntries(Object.keys(THEME_DEFAULTS).map(key => [key,$('#theme-'+key).value]));
  savedTheme = (await api('/theme',{method:'PATCH',data:{version:savedTheme.version,colors}})).theme;
  applyTheme(savedTheme.colors); $('#theme-status').textContent='Cores salvas para todos os usuários.';
  message('Cores salvas.');
}); };
$('#activity-menu').onclick = () => run(showActivity);
$('#activity-back').onclick = () => run(() => loadWorkspace());
$('#activity-filters').onsubmit = event => { event.preventDefault(); run(() => loadActivity()); };
$('#activity-clear').onclick = () => run(async () => { $('#activity-filters').reset(); await loadActivity(); });
$('#activity-more').onclick = () => run(() => loadActivity(true));
$('#history-close').onclick = () => $('#history-dialog').close();
$('#back-board').onclick = () => run(() => loadWorkspace());
$('#refresh').onclick = () => run(async () => { await loadWorkspace(); message("Quadro atualizado."); });
$('#board-select').onchange = () => run(() => loadWorkspace($('#board-select').value));
$('#search').oninput = render;
$('#assignee-filter').onchange = () => { onlyMine = false; $('#my-tasks').setAttribute('aria-pressed', 'false'); render(); };
$('#my-tasks').onclick = () => {
  onlyMine = !onlyMine; $('#my-tasks').setAttribute('aria-pressed', String(onlyMine));
  if (onlyMine) $('#assignee-filter').value = ''; render();
};
$('#new').onclick = () => run(() => openTask());
$('#assignees').onchange = () => {
  if (!editing) return;
  const selected = [...$('#assignees').selectedOptions].map(option => option.value);
  queueTaskAction(() => {
    const current = board.tasks.find(t => t.id === editing).assignees;
    return changeAssignments(selected.filter(id => !current.includes(id)), current.filter(id => !selected.includes(id)));
  });
};
$('#assign-self').onchange = () => {
  const assigned = $('#assign-self').checked;
  if (editing) queueTaskAction(() => changeAssignments(assigned ? [user.id] : [], assigned ? [] : [user.id]));
};
$('#assignment-add').onclick = () => { const id = $('#assignment-person').value; queueTaskAction(() => changeAssignments([id], [])); };
$('#assignment-remove').onclick = () => { const id = $('#assignment-person').value; queueTaskAction(() => changeAssignments([], [id])); };
async function closeTask() {
  if (pendingFiles.length) {
    if (!editing) { if (!confirm('Descartar os arquivos selecionados e fechar a nova tarefa?')) return; pendingFiles = []; }
    else await uploadPendingFiles();
  }
  clearTimeout(autosaveTimer);
  if (editing && can('edit_tasks') && JSON.stringify(taskFields()) !== savedTaskFields) {
    if (autosaveBlocked) {
      if (!confirm("Seu rascunho não foi salvo. Deseja descartá-lo e fechar?")) return;
    } else {
      try { await saveTaskDraft(); }
      catch (error) { autosaveBlocked = true; throw error; }
    }
  }
  $('#dialog').close();
}
$('#cancel').onclick = () => run(closeTask);
$('#dialog').addEventListener('cancel', event => { event.preventDefault(); run(closeTask); });
for (const id of ['title', 'description', 'task-date', 'desired-delivery']) {
  $('#' + id).addEventListener('input', scheduleAutosave);
  $('#' + id).addEventListener('change', scheduleAutosave);
}
$('#form').onsubmit = event => { event.preventDefault(); run(async () => {
  clearTimeout(autosaveTimer);
  if (editing) { await saveTaskDraft(); await uploadPendingFiles(); $('#dialog').close(); return; }
  const data = taskFields();
  if (!editing) data.column_id = $('#status').value;
  const result = await mutate(editing ? '/tasks/' + editing : '/boards/' + board.id + '/tasks', editing ? 'PATCH' : 'POST', data, taskVersion);
  if (!editing) { editing = result.task_id; taskVersion = board.version; await openTask(board.tasks.find(t => t.id === editing), undefined, true);
    try { await uploadPendingFiles(); }
    catch (error) { error.message = 'Tarefa criada. ' + error.message; throw error; } }
}); };
$('#upload-form').onsubmit = event => event.preventDefault();
$('#retry-upload').onclick = () => queueTaskAction(uploadPendingFiles);
$('#files').onchange = () => {
  const selected = [...$('#files').files];
  $('#files').value = '';
  if (!selected.length) return;
  const invalid = selected.find(file => file.size === 0 || file.size > config.max_file_bytes);
  if (invalid) { run(() => { throw new Error(invalid.name + (invalid.size === 0 ? ' está vazio.' : ' excede o tamanho máximo permitido.')); }); return; }
  pendingFiles.push(...selected);
  uploadFailed = false;
  renderPendingFiles();
  if (editing) queueTaskAction(uploadPendingFiles);
};
$('#new-column').onclick = () => openColumn();
$('#column-cancel').onclick = () => $('#column-dialog').close();
$('#column-form').onsubmit = event => { event.preventDefault(); run(async () => {
  await mutate(editingColumn ? '/columns/' + editingColumn : '/boards/' + board.id + '/columns', editingColumn ? 'PATCH' : 'POST',
    {name: $('#column-name').value.trim(), type: $('#column-type').value}, columnVersion);
  $('#column-dialog').close();
}); };
$('#remove-column').onclick = () => run(async () => {
  if (!confirm("Excluir esta coluna e mover as tarefas para a coluna selecionada?")) return;
  await mutate('/columns/' + editingColumn, 'DELETE', {target_id: $('#move-tasks').value}, columnVersion);
  $('#column-dialog').close();
});
$('#new-board').onclick = () => openBoard(); $('#rename-board').onclick = () => openBoard(true);
$('#board-cancel').onclick = () => $('#board-dialog').close();
$('#board-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const result = await api(editingBoard ? '/boards/' + board.id : '/boards', {method: editingBoard ? 'PATCH' : 'POST',
    data: {name: $('#board-name').value.trim(), version: boardVersion}});
  $('#board-dialog').close(); await loadWorkspace(result.board.id); message("Quadro salvo.");
}); };
$('#delete-board').onclick = () => run(async () => {
  if (!confirm("Excluir “" + board.name + "”, todas as suas tarefas e os arquivos anexados?")) return;
  await api('/boards/' + board.id, {method: 'DELETE', data: {version: board.version}});
  board = null; await loadWorkspace(); message("Quadro excluído.");
});
$('#new-user').onclick = () => openUser(); $('#user-cancel').onclick = () => $('#user-dialog').close();
$('#user-role').onchange = () => drawPermissions($('#user-role').value);
$('#role-defaults').onclick = () => drawPermissions($('#user-role').value);
$('#user-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const data = {};
  if (can('manage_users')) { data.username = $('#user-name').value.trim(); data.enabled = $('#user-enabled').value === 'true'; }
  if (can('manage_permissions')) {
    data.role = $('#user-role').value; data.overrides = {};
    for (const input of $('#permissions').querySelectorAll('input')) {
      const key = input.dataset.permission, roleDefault = config.roles[data.role].includes(key);
      if (data.role !== 'Administrator' && input.checked !== roleDefault) data.overrides[key] = input.checked;
    }
  }
  if (!editingUser) data.password = $('#temporary-password').value;
  else data.version = editingUser.version;
  await api(editingUser ? '/users/' + editingUser.id : '/users', {method: editingUser ? 'PATCH' : 'POST', data});
  $('#temporary-password').value = ''; $('#user-dialog').close(); await showAdmin(); message("Conta salva.");
}); };
$('#delete-user').onclick = () => run(async () => {
  if (!confirm("Excluir “" + editingUser.username + "”? As tarefas e os arquivos dessa pessoa serão mantidos.")) return;
  await api('/users/' + editingUser.id, {method: 'DELETE', data: {version: editingUser.version}});
  $('#user-dialog').close(); await showAdmin(); message("Conta excluída. As tarefas e os arquivos foram mantidos.");
});
$('#reset-user-password').onclick = () => run(async () => {
  const password = $('#temporary-password').value;
  if (password.length < 10 || password.length > 256) throw new Error("Informe uma senha temporária de 10 a 256 caracteres.");
  if (!confirm("Redefinir a senha deste usuário e encerrar seus acessos ativos?")) return;
  await api('/users/' + editingUser.id + '/password', {method: 'POST', data: {password, version: editingUser.version}});
  $('#temporary-password').value = ''; $('#user-dialog').close(); await showAdmin(); message("Senha redefinida. O usuário deverá alterá-la no próximo acesso.");
});
$('#import-browser').onclick = () => run(async () => { const data = legacyBoard(); if (data) await importBoard(data); });
$('#import-file').onchange = () => run(async () => {
  const file = $('#import-file').files[0]; if (!file) return;
  if (file.size > 1024 * 1024 * 10) throw new Error("O arquivo de importação deve ter menos de 10 MB.");
  let payload;
  try { payload = JSON.parse(await file.text()); } catch { throw new Error("O arquivo de importação não é um JSON válido."); }
  await importBoard(payload); $('#import-file').value = '';
});
setInterval(() => {
  if (pending || !user || user.must_change_password || !board || $('#workspace').hidden || document.querySelector('dialog[open]') || document.hidden) return;
  run(async () => {
    const me = await api('/me');
    user = me.user; csrf = me.csrf; renderIdentity();
    savedTheme = (await api('/theme')).theme; applyTheme(savedTheme.colors);
    if (user.must_change_password) { showPassword(); return; }
    const list = (await api('/boards')).boards;
    const directory = (await api('/people')).users;
    const identitiesChanged = JSON.stringify(directory)!==JSON.stringify(people);
    people=directory;
    const oldTags=JSON.stringify(tagRegistry);await loadTags();
    if(oldTags!==JSON.stringify(tagRegistry)){renderTagFilters();render();}
    const current = list.find(b => b.id === board.id);
    if (!current || current.version !== board.version || JSON.stringify(list) !== JSON.stringify(boards)) await loadWorkspace();
    else if(identitiesChanged) {
      const selected=$('#assignee-filter').value;
      options($('#assignee-filter'),[['','Todos os responsáveis'],...people.map(person=>[person.id,personName(person)])],selected);
      render();
    }
  });
}, 5000);
run(async () => {
  if (location.protocol === 'file:') {
    showLogin(); message("Inicie o servidor com python app.py na pasta kanban e acesse http://127.0.0.1:8000.", true); return;
  }
  try { await signedIn(await api('/me')); }
  catch (error) { showLogin(); if (error.status !== 401) message(error.message, true); }
});
document.addEventListener('invalid', event => {
  const input = event.target;
  if (!input.setCustomValidity) return;
  if (input.validity.valueMissing) input.setCustomValidity('Preencha este campo.');
  else if (input.validity.tooShort) input.setCustomValidity('Use pelo menos ' + input.minLength + ' caracteres.');
  else if (input.validity.patternMismatch) input.setCustomValidity('Use apenas letras, números, pontos, sublinhados e hífens.');
}, true);
for (const name of ['input', 'change']) document.addEventListener(name, event => {
  if (event.target.setCustomValidity) event.target.setCustomValidity('');
});

$('#update-check').onclick=()=>run(async()=>{await api('/updates/check',{method:'POST',data:{}});await loadUpdates();message('Consulta concluída.');});
$('#update-download').onclick=()=>run(async()=>{
  $('#update-progress').hidden=false; $('#update-progress').removeAttribute('value'); $('#update-progress-label').textContent='Baixando e validando pacote…';
  try { await api('/updates/download',{method:'POST',data:{}}); }
  finally { await loadUpdates(); }
});
for(const action of ['install','rollback']) $('#update-'+action).onclick=()=>run(async()=>{
  const target=action==='rollback'?updateInfo?.previous:updateInfo?.downloaded;
  if(!target) return;
  if(!confirm(action==='rollback'?'Restaurar a versão '+target.version+'? O servidor será reiniciado. O banco atual será mantido; esquemas incompatíveis bloqueiam a restauração.':'Instalar a versão '+target.version+'? Será feito um backup. O servidor ficará temporariamente indisponível durante a reinicialização.')) return;
  await api('/updates/'+action,{method:'POST',data:{confirm:true,version:target.version}});
  watchUpdate();
});
$('#update-history-button').onclick=()=>run(async()=>{
  const {entries}=await api('/updates/history');
  const target=$('#update-history');target.replaceChildren();target.hidden=false;
  if(!entries.length) target.append(el('p','field-hint','Nenhuma instalação registrada.'));
  for(const item of entries) target.append(el('p','',new Date(item.finished_at).toLocaleString('pt-BR')+' ? '+item.actor_name+' ? '+item.from+' ? '+item.to+' ? '+item.message));
});
$('#update-settings-form').onsubmit=event=>{event.preventDefault();run(async()=>{
  if(!updateInfo) {message('É necessária a permissão de visualizar atualizações para editar a configuração.',true);return;}
  await api('/updates/settings',{method:'PATCH',data:{version:updateInfo.settings.version,owner:$('#update-owner').value.trim(),repository:$('#update-repository').value.trim(),channel:$('#update-channel').value}});
  await loadUpdates();message('Repositório salvo.');
});};
