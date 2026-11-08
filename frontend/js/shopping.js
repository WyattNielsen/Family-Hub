document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('shopForm').addEventListener('submit', addItem);
  document.getElementById('clearCheckedBtn').addEventListener('click', clearChecked);
  loadShopping();
});

async function loadShopping() {
  const el = document.getElementById('shopList');
  try {
    const items = await API.get('/api/shopping/');
    if (!items.length) {
      el.innerHTML = '<div class="empty-state">The list is empty. Add something to pick up.</div>';
      return;
    }
    el.innerHTML = items.map(item => `
      <div class="shop-item${item.checked ? ' checked' : ''}">
        <button type="button" class="shop-check" onclick="toggleShopItem(${item.id}, ${item.checked ? 'true' : 'false'})" aria-label="Toggle ${escapeHtml(item.name)}">${item.checked ? '✓' : ''}</button>
        <div class="shop-name">${escapeHtml(item.name)}</div>
        ${item.quantity ? `<div class="shop-qty">${escapeHtml(item.quantity)}</div>` : ''}
        <button type="button" class="shop-delete" onclick="deleteShopItem(${item.id})" aria-label="Delete"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg></button>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Could not load the shopping list</div>';
  }
}

async function addItem(ev) {
  ev.preventDefault();
  const nameEl = document.getElementById('shopName');
  const qtyEl = document.getElementById('shopQty');
  const name = nameEl.value.trim();
  if (!name) return;
  try {
    await API.post('/api/shopping/', { name, quantity: qtyEl.value.trim() });
    nameEl.value = '';
    qtyEl.value = '';
    nameEl.focus();
    loadShopping();
  } catch (e) {
    showToast('Could not add that item', 'error');
  }
}

async function toggleShopItem(id, checked) {
  try {
    await API.patch('/api/shopping/' + id, { checked: !checked });
    loadShopping();
  } catch (e) {
    showToast('Could not update that item', 'error');
  }
}

async function deleteShopItem(id) {
  try {
    await API.delete('/api/shopping/' + id);
    loadShopping();
  } catch (e) {
    showToast('Could not delete that item', 'error');
  }
}

async function clearChecked() {
  try {
    await API.post('/api/shopping/clear-checked', {});
    loadShopping();
  } catch (e) {
    showToast('Could not clear completed items', 'error');
  }
}
