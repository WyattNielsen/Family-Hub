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
        <button type="button" class="chore-action-btn delete" onclick="deleteShopItem(${item.id})" aria-label="Delete">🗑</button>
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
