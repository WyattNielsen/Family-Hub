let _members = [];

function timeAgo(isoStr) {
  const diff = Date.now() - new Date(isoStr + 'Z').getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function renderMessage(msg) {
  const color   = msg.color || '#9AA0B8';
  const avatar  = msg.avatar || '👤';
  const name    = msg.name  || 'Anonymous';
  const bg      = msg.color ? msg.color + '18' : 'var(--surface2)';
  return `
    <div class="msg-card" style="border-left:4px solid ${color};background:${bg}" id="msg-${msg.id}">
      <div class="msg-card-header">
        <span class="msg-avatar">${avatar}</span>
        <span class="msg-author" style="color:${color}">${name}</span>
        <span class="msg-time">${timeAgo(msg.created_at)}</span>
        <button class="msg-delete" onclick="deleteMessage(${msg.id})" title="Dismiss">✕</button>
      </div>
      <div class="msg-body">${escapeHtml(msg.body)}</div>
    </div>`;
}

function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

async function loadMessages() {
  try {
    const msgs = await API.get('/api/messages/');
    const board = document.getElementById('msgBoard');
    const empty = document.getElementById('msgEmpty');
    if (!msgs.length) {
      board.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    board.innerHTML = msgs.map(renderMessage).join('');
  } catch(e) {
    console.error('Failed to load messages', e);
  }
}

async function postMessage() {
  const body   = document.getElementById('msgBody').value.trim();
  const author = document.getElementById('msgAuthor').value;
  if (!body) { showToast('Write a message first', 'error'); return; }
  try {
    await API.post('/api/messages/', {
      body,
      author_id: author ? parseInt(author) : null
    });
    document.getElementById('msgBody').value = '';
    await loadMessages();
    showToast('Message posted!', 'success');
  } catch(e) {
    showToast('Failed to post message', 'error');
  }
}

async function deleteMessage(id) {
  try {
    await API.delete(`/api/messages/${id}`);
    document.getElementById(`msg-${id}`)?.remove();
    const board = document.getElementById('msgBoard');
    if (!board.children.length) {
      document.getElementById('msgEmpty').style.display = 'block';
    }
  } catch(e) {
    showToast('Failed to remove message', 'error');
  }
}

async function loadMemberOptions() {
  try {
    _members = await API.get('/api/members/');
    const sel = document.getElementById('msgAuthor');
    sel.innerHTML = '<option value="">— Who\'s posting? —</option>' +
      _members.map(m => `<option value="${m.id}">${m.avatar || '👤'} ${m.name}</option>`).join('');
  } catch(e) {}
}

// Send on Ctrl/Cmd+Enter
document.addEventListener('DOMContentLoaded', async () => {
  await loadMemberOptions();
  await loadMessages();
  // Refresh times every minute
  setInterval(loadMessages, 60000);

  document.getElementById('msgBody').addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') postMessage();
  });
});
