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
    
    // --- NEW DYNAMIC GALLERY LOGIC ---
    const galleryContainer = document.querySelector('.gallery-container');
    if (galleryContainer) {
        const navLinks = document.querySelectorAll('.nav-link');
        const renderingTitle = document.getElementById('rendering-title');
        const renderingGrid = document.getElementById('rendering-grid');
        const optionsTitle = document.getElementById('options-title');
        const optionsDescription = document.getElementById('options-description');
        const optionsDropdowns = document.getElementById('options-dropdowns');
        const modifyForm = document.getElementById('modifyForm');
        const activeCategoryInput = document.getElementById('active-category');
        const describeChangesTextarea = document.getElementById('describe-changes');
        const darkModeSwitch = document.getElementById('darkModeSwitch');

        function updateDisplay(category) {
            activeCategoryInput.value = category;

            navLinks.forEach(link => {
                link.classList.toggle('active', link.dataset.category === category);
            });

            renderingTitle.textContent = `Renderings for ${category}`;
            optionsTitle.textContent = category;
            
            renderingGrid.innerHTML = '';
            optionsDropdowns.innerHTML = '';
            
            const renderings = ALL_RENDERINGS[category] || [];
            if (renderings.length > 0) {
                renderings.forEach(r => {
                    const card = document.createElement('div');
                    card.className = 'rendering-card-main';
                    // The image path needs to be prefixed with '/static/'
                    card.innerHTML = `<img src="/static/${r.image_path}" alt="${r.subcategory}">`;
                    renderingGrid.appendChild(card);
                });
            } else {
                renderingGrid.innerHTML = `<p class="no-renderings-msg">No renderings yet for ${category}. Use the panel on the right to generate one!</p>`;
            }

            const options = ALL_OPTIONS[category] || {};
            for (const [opt, vals] of Object.entries(options)) {
                const label = document.createElement('label');
                label.textContent = opt;
                const select = document.createElement('select');
                select.name = opt;
                select.innerHTML = `<option value="">Default</option>` + vals.map(v => `<option value="${v}">${v}</option>`).join('');
                label.appendChild(select);
                optionsDropdowns.appendChild(label);
            }
            
            if (category === "Back Exterior") {
                optionsDescription.textContent = "The Back Exterior features a brand-new deck, lush greenery, and walk-out access from the finished basement.";
            } else {
                optionsDescription.textContent = "";
            }
            // Apply dark mode if it was already active
            document.querySelectorAll('.rendering-card-main img').forEach(img => {
                img.classList.toggle('dark', darkModeSwitch.checked);
            });
        }

        navLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                updateDisplay(link.dataset.category);
            });
        });

        modifyForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(modifyForm);
            const button = modifyForm.querySelector('button[type="submit"]');
            button.textContent = 'Generating...';
            button.disabled = true;
            document.getElementById('loadingOverlay').style.display = 'flex';

            try {
                // Use generate_room endpoint for all new renderings from this panel
                const response = await fetch('/generate_room', { method: 'POST', body: formData });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error);
                window.location.reload(); // Simple reload is easiest to manage state
            } catch (error) {
                alert(`Error: ${error.message}`);
                document.getElementById('loadingOverlay').style.display = 'none';
                button.textContent = 'Generate Rendering';
                button.disabled = false;
            }
        });
        
        darkModeSwitch.addEventListener('change', () => {
            document.querySelectorAll('.rendering-card-main img').forEach(img => {
                img.classList.toggle('dark', darkModeSwitch.checked);
            });
        });

        // Voice prompt for the right panel
        const voiceBtnRight = document.getElementById('voiceBtnRight');
        const describeChanges = document.getElementById('describe-changes');
        if (voiceBtnRight && describeChanges) {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (SpeechRecognition) {
                const recognition = new SpeechRecognition();
                recognition.onresult = (event) => { describeChanges.value = event.results[0][0].transcript; };
                voiceBtnRight.addEventListener('click', () => recognition.start());
            } else {
                voiceBtnRight.style.display = 'none';
            }
        }
        
        // Initial load
        if (navLinks.length > 0) {
            updateDisplay('Front Exterior');
        }
    }
});

function showFlash(message, category) {
    const container = document.getElementById('flash-container');
    const flash = document.createElement('div');
    flash.className = `flash ${category}`;
    flash.textContent = message;
    container.prepend(flash);
    setTimeout(() => flash.remove(), 5000);
}
