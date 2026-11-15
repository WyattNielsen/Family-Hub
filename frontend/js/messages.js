(function () {
  let _members = [];
  let _msgTimer = null;

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

  function _paintMessages(allMsgs) {
    const board = document.getElementById('msgBoard');
    const empty = document.getElementById('msgEmpty');
    if (!board || !empty) return;
    const msgs = messagesForPerson(allMsgs);
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
  }

  async function loadMessages() {
    const cached = API.peek('/api/messages/');
    if (cached) _paintMessages(cached);
    try {
      const msgs = await API.get('/api/messages/');
      if (FHRouter.currentRoute() === '/messages.html') _paintMessages(msgs);
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
        author_id: author ? parseInt(author, 10) : null,
        recipient_id: recipient ? parseInt(recipient, 10) : null,
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
      if (board && !board.children.length) {
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
    const cached = API.peek('/api/members/');
    if (cached) {
      _members = cached;
      const person = getPersonFilter();
      fillPersonSelect(document.getElementById('msgRecipient'), 'Everyone', person === 'all' ? '' : person);
      fillPersonSelect(document.getElementById('msgAuthor'), "Who's posting?");
    }
    try {
      _members = await API.get('/api/members/');
      if (FHRouter.currentRoute() !== '/messages.html') return;
      const person = getPersonFilter();
      fillPersonSelect(document.getElementById('msgRecipient'), 'Everyone', person === 'all' ? '' : person);
      fillPersonSelect(document.getElementById('msgAuthor'), "Who's posting?");
    } catch (e) {}
  }

  window.postMessage = postMessage;
  window.ackMessage = ackMessage;

  function mount() {
    onPersonFilterChange(() => {
      const person = getPersonFilter();
      const sel = document.getElementById('msgRecipient');
      if (sel) sel.value = person === 'all' ? '' : person;
      // Instant client-side filter from cached Everyone data — no network.
      const cached = API.peek('/api/messages/');
      if (cached) _paintMessages(cached);
      else loadMessages();
    });
    loadMemberOptions();
    loadMessages();
    _msgTimer = setInterval(() => {
      if (!document.hidden) loadMessages();
    }, 60000);

    document.getElementById('msgBody').addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') postMessage();
    });

    return unmount;
  }

  function unmount() {
    clearInterval(_msgTimer);
    _msgTimer = null;
  }

  FHRouter.register('/messages.html', { mount, unmount });
})();
