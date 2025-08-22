document.addEventListener('DOMContentLoaded', function() {
    // --- Universal Modal Logic ---
    const modal = document.getElementById('imageModal');
    if (modal) {
        document.addEventListener('click', e => {
            if (e.target.classList.contains('modal-trigger')) {
                modal.style.display = 'block'; document.getElementById('modalImg').src = e.target.src;
            }
            if (e.target.classList.contains('close-modal')) {
                modal.style.display = 'none';
            }
        });
        window.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
    }

    // --- Index Page Logic (Voice, File Upload & Loading Overlay) ---
    const generateForm = document.getElementById('generateForm');
    if (generateForm) {
        generateForm.addEventListener('submit', function(e) {
            const description = document.getElementById('description');
            if (!description.value.trim()) {
                alert('Please provide a home description before generating.');
                e.preventDefault();
                return;
            }
            document.getElementById('loadingOverlay').style.display = 'flex';
        });

        const voiceBtn = document.getElementById('voiceBtn');
        const description = document.getElementById('description');
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            const recognition = new SpeechRecognition();
            recognition.onresult = (event) => { description.value = event.results[0][0].transcript; };
            voiceBtn.addEventListener('click', () => recognition.start());
        } else {
            if(voiceBtn) voiceBtn.style.display = 'none';
        }
        
        const planFileInput = document.getElementById('planFileInput');
        const fileNameDisplay = document.getElementById('fileNameDisplay');
        if(planFileInput && fileNameDisplay) {
            planFileInput.addEventListener('change', () => {
                if (planFileInput.files.length > 0) {
                    fileNameDisplay.textContent = `Plan loaded: ${planFileInput.files[0].name}`;
                } else {
                    fileNameDisplay.textContent = '';
                }
            });
        }
    }
    
    // --- Gallery / Session Gallery Page Logic ---
    const gridContainer = document.getElementById('renderingsGrid');
    if (gridContainer) {
        const roomForm = document.getElementById('generateRoomForm');
        const roomSelect = document.getElementById('roomSelect');
        const roomOptionsContainer = document.getElementById('roomOptionsContainer');
        
        function updateRoomOptions() {
            if (!roomSelect) return;
            const subcategory = roomSelect.value;
            const options = ROOM_OPTIONS[subcategory];
            roomOptionsContainer.innerHTML = '';
            if (options) {
                const container = document.createElement('div');
                container.className = 'options-grid';
                for (const [opt, vals] of Object.entries(options)) {
                    const label = document.createElement('label');
                    label.textContent = opt;
                    const select = document.createElement('select');
                    select.name = opt;
                    vals.forEach(v => {
                        const option = document.createElement('option');
                        option.value = v;
                        option.textContent = v;
                        select.appendChild(option);
                    });
                    label.appendChild(select);
                    container.appendChild(label);
                }
                roomOptionsContainer.appendChild(container);
            }
        }
        
        if (roomSelect) {
            roomSelect.addEventListener('change', updateRoomOptions);
            updateRoomOptions();
        }
        
        document.body.addEventListener('submit', handleFormSubmit);
        document.body.addEventListener('click', handleCardClick);

        const deleteBtn = document.getElementById('deleteBtn');
        if(deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                const selectedIds = getSelectedIds();
                if (selectedIds.length === 0) {
                    alert('Please select one or more renderings to delete.');
                    return;
                }
                if (confirm(`Are you sure you want to delete ${selectedIds.length} rendering(s)?`)) {
                    handleBulkAction('delete', selectedIds, true);
                }
            });
        }

        const selectAll = document.getElementById('selectAll');
        if(selectAll) {
            selectAll.addEventListener('change', (e) => {
                document.querySelectorAll('.rendering-checkbox').forEach(cb => {
                    cb.checked = e.target.checked;
                });
            });
        }
    }
});

function getSelectedIds() {
    return Array.from(document.querySelectorAll('.rendering-checkbox:checked'))
                .map(cb => cb.closest('.render-card').dataset.id);
}

function handleFormSubmit(e) {
    if (e.target.classList.contains('modify-form')) {
        e.preventDefault();
        modifyRendering(e.target);
    }
    if (e.target.id === 'generateRoomForm') {
        e.preventDefault();
        generateNewRoom(e.target);
    }
}

function handleCardClick(e) {
    const card = e.target.closest('.render-card');
    if (!card) return;

    if (e.target.classList.contains('like-btn') || e.target.classList.contains('fav-btn')) {
        if (requireLogin('save likes and favorites')) return;
        const action = e.target.classList.contains('like-btn') ? 'like' : 'favorite';
        handleBulkAction(action, [card.dataset.id]).then(() => e.target.classList.toggle('active'));
    } else if (e.target.classList.contains('dark-toggle')) {
        card.querySelector('.render-img').classList.toggle('dark');
    } else if (e.target.classList.contains('delete-session-btn')) {
        if (confirm('Are you sure you want to remove this rendering from your session?')) {
            deleteSessionRendering(card.dataset.id);
        }
    }
}

async function deleteSessionRendering(id) {
    try {
        const response = await fetch(`/delete_session_rendering/${id}`, { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        showFlash(result.message, 'success');
        document.querySelector(`.render-card[data-id='${id}']`).remove();
    } catch (error) {
        showFlash(error.message, 'danger');
    }
}

async function handleBulkAction(action, ids, reloadPage = false) {
    const body = new FormData();
    body.append('action', action);
    body.append('ids', JSON.stringify(ids));

    try {
        const response = await fetch('/bulk_action', { method: 'POST', body: body });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        showFlash(result.message, 'success');
        if (reloadPage) {
            setTimeout(() => window.location.reload(), 1500);
        }
    } catch (error) {
        showFlash(error.message, 'danger');
    }
}

async function modifyRendering(form) {
    const id = form.dataset.id;
    const formData = new FormData(form);
    const button = form.querySelector('button');
    button.textContent = 'Generating...';
    button.disabled = true;
    document.getElementById('loadingOverlay').style.display = 'flex';

    try {
        const response = await fetch(`/modify_rendering/${id}`, { method: 'POST', body: formData });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        showFlash(result.message, 'success');
        setTimeout(() => window.location.reload(), 1500);
    } catch (error) {
        showFlash(error.message, 'danger');
        document.getElementById('loadingOverlay').style.display = 'none';
    } finally {
        button.textContent = 'Regenerate';
        button.disabled = false;
    }
}

async function generateNewRoom(form) {
    const formData = new FormData(form);
    const button = form.querySelector('button');
    button.textContent = 'Generating...';
    button.disabled = true;
    document.getElementById('loadingOverlay').style.display = 'flex';

    try {
        const response = await fetch('/generate_room', { method: 'POST', body: formData });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        showFlash(result.message, 'success');
        setTimeout(() => window.location.reload(), 1500);
    } catch (error) {
        showFlash(error.message, 'danger');
        document.getElementById('loadingOverlay').style.display = 'none';
    } finally {
        button.textContent = 'Generate Room';
        button.disabled = false;
    }
}

function requireLogin(action_text = 'save your work') {
    if (!IS_LOGGED_IN) {
        if (confirm(`Please log in or register to ${action_text}. Would you like to go to the login page?`)) {
            window.location.href = '/login?next=' + window.location.pathname;
        }
        return true;
    }
    return false;
}

function showFlash(message, category) {
    const container = document.getElementById('flash-container');
    const flash = document.createElement('div');
    flash.className = `flash ${category}`;
    flash.textContent = message;
    container.prepend(flash);
    setTimeout(() => flash.remove(), 5000);
}
