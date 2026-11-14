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

function messageHtmlEscape(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/\n/g,'<br>');
}

function messagesForPerson(msgs) {
  const person = getPersonFilter();
  if (person === 'all') return msgs;
  return msgs.filter(m => !m.recipient_id || String(m.recipient_id) === String(person));
}

function renderMessage(msg) {
  const to = msg.recipient_id ? {
    name: msg.recipient_name || 'Someone',
    color: msg.recipient_color || '#9AA0B8',
    avatar: msg.recipient_avatar,
    avatar_photo: msg.recipient_avatar_photo,
  } : { name: 'Everyone', color: '#9AA0B8', avatar: '👨‍👩‍👧‍👦' };
  const from = msg.name || 'Someone';
  return `
    <div class="msg-card" style="--event:${to.color}" id="msg-${msg.id}">
      <div class="msg-card-top">
        <div class="msg-to">
          ${memberAvatarHtml(to, 'avatar-lg')}
          <span class="msg-to-name" style="color:${to.color}">${messageHtmlEscape(to.name)}</span>
        </div>
        <button class="msg-ack" onclick="ackMessage(${msg.id})">Got it</button>
      </div>
      <div class="msg-body">${messageHtmlEscape(msg.body)}</div>
      <div class="msg-by">— ${messageHtmlEscape(from)} · ${timeAgo(msg.created_at)}</div>
    </div>`;
}

async function loadMessages() {
  try {
    const msgs = messagesForPerson(await API.get('/api/messages/'));
    const board = document.getElementById('msgBoard');
    const empty = document.getElementById('msgEmpty');
    if (!msgs.length) {
      board.innerHTML = '';
      empty.style.display = 'block';
      empty.textContent = getPersonFilter() === 'all'
        ? 'No messages yet. Leave a note for someone.'
        : 'No messages for this person.';
      return;
    }
    empty.style.display = 'none';
    board.innerHTML = msgs.map(renderMessage).join('');
  } catch (e) {
    console.error('Failed to load messages', e);
  }
}

function fillPersonSelect(sel, placeholder, selected) {
  if (!sel) return;
  sel.innerHTML = `<option value="">${placeholder}</option>` +
    _members.map(m => `<option value="${m.id}">${m.avatar || '👤'} ${messageHtmlEscape(m.name)}</option>`).join('');
  if (selected) sel.value = String(selected);
}

async function postMessage() {
  const body = document.getElementById('msgBody').value.trim();
  const author = document.getElementById('msgAuthor').value;
  const recipient = document.getElementById('msgRecipient').value;
  if (!body) { showToast('Write a message first', 'error'); return; }
  try {
    await API.post('/api/messages/', {
      body,
      author_id: author ? parseInt(author) : null,
      recipient_id: recipient ? parseInt(recipient) : null,
    });
    document.getElementById('msgBody').value = '';
    await loadMessages();
    loadMessageBadge();
    showToast('Message posted!', 'success');
  } catch (e) {
    showToast('Failed to post message', 'error');
  }
}

async function ackMessage(id) {
  try {
    await API.delete(`/api/messages/${id}`);
    document.getElementById(`msg-${id}`)?.remove();
    const board = document.getElementById('msgBoard');
    if (!board.children.length) {
      const empty = document.getElementById('msgEmpty');
      empty.style.display = 'block';
      empty.textContent = getPersonFilter() === 'all'
        ? 'No messages yet. Leave a note for someone.'
        : 'No messages for this person.';
    }
    loadMessageBadge();
  } catch (e) {
    showToast('Could not clear that message', 'error');
  }
}

async function loadMemberOptions() {
  try {
    _members = await API.get('/api/members/');
    const person = getPersonFilter();
    fillPersonSelect(document.getElementById('msgRecipient'), 'Everyone', person === 'all' ? '' : person);
    fillPersonSelect(document.getElementById('msgAuthor'), "Who's posting?");
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  onPersonFilterChange(() => {
    const person = getPersonFilter();
    const sel = document.getElementById('msgRecipient');
    if (sel) sel.value = person === 'all' ? '' : person;
    loadMessages();
  });
  loadMemberOptions();
  loadMessages();
  setInterval(loadMessages, 60000);

  document.getElementById('msgBody').addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') postMessage();
  });
});
