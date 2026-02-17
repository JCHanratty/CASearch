/**
 * Search Settings JavaScript
 * Handles toggle state persistence and smooth UX transitions
 */

// LocalStorage keys
const STORAGE_KEYS = {
    EXPAND_SYNONYMS: 'searchSettings_expandSynonyms'
};

/**
 * Initialize search settings from localStorage
 */
function initSearchSettings() {
    // Load synonym expansion preference
    const expandSynonyms = localStorage.getItem(STORAGE_KEYS.EXPAND_SYNONYMS) === 'true';

    // Update sidebar toggle
    const sidebarToggle = document.getElementById('sidebar-expand-synonyms');
    if (sidebarToggle) {
        sidebarToggle.checked = expandSynonyms;
    }

    // Update search form toggle
    const searchFormToggle = document.getElementById('expand_synonyms');
    if (searchFormToggle) {
        searchFormToggle.checked = expandSynonyms;
    }
}

/**
 * Save synonym expansion preference to localStorage
 * @param {boolean} enabled - Whether synonym expansion is enabled
 */
function saveSynonymPreference(enabled) {
    localStorage.setItem(STORAGE_KEYS.EXPAND_SYNONYMS, enabled.toString());

    // Sync with search form toggle if on search page
    const searchFormToggle = document.getElementById('expand_synonyms');
    if (searchFormToggle && searchFormToggle.checked !== enabled) {
        searchFormToggle.checked = enabled;
    }

    // Sync with sidebar toggle
    const sidebarToggle = document.getElementById('sidebar-expand-synonyms');
    if (sidebarToggle && sidebarToggle.checked !== enabled) {
        sidebarToggle.checked = enabled;
    }

    // Show toast notification
    showToast(enabled ? 'Synonym expansion enabled' : 'Synonym expansion disabled');
}

/**
 * Show a toast notification
 * @param {string} message - Message to display
 * @param {string} type - Type of toast ('success', 'info', 'warning')
 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `px-4 py-3 rounded-lg shadow-lg text-sm font-medium transform transition-all duration-300 translate-x-full opacity-0 ${
        type === 'success' ? 'bg-green-600 text-white' :
        type === 'warning' ? 'bg-yellow-500 text-white' :
        'bg-slate-700 text-white'
    }`;
    toast.textContent = message;

    container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    });

    // Remove toast after delay
    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

/**
 * Handle HTMX beforeRequest to inject synonym expansion parameter
 */
function setupHtmxIntegration() {
    document.body.addEventListener('htmx:configRequest', function(event) {
        // Check if this is a search request
        if (event.detail.path && event.detail.path.includes('/search')) {
            // Get current synonym expansion setting
            const searchFormToggle = document.getElementById('expand_synonyms');
            const sidebarToggle = document.getElementById('sidebar-expand-synonyms');

            // Use form toggle if available, otherwise use sidebar setting
            const expandSynonyms = searchFormToggle
                ? searchFormToggle.checked
                : (sidebarToggle ? sidebarToggle.checked : localStorage.getItem(STORAGE_KEYS.EXPAND_SYNONYMS) === 'true');

            // Ensure expand_synonyms parameter is included
            if (expandSynonyms) {
                event.detail.parameters['expand_synonyms'] = 'true';
            }
        }
    });

    // After HTMX swaps content, re-sync toggles
    document.body.addEventListener('htmx:afterSwap', function(event) {
        const expandSynonyms = localStorage.getItem(STORAGE_KEYS.EXPAND_SYNONYMS) === 'true';

        // Re-sync search form toggle after swap
        const searchFormToggle = document.getElementById('expand_synonyms');
        if (searchFormToggle) {
            searchFormToggle.checked = expandSynonyms;
        }
    });
}

/**
 * Setup form toggle change handler for bidirectional sync
 */
function setupFormToggleSync() {
    const searchFormToggle = document.getElementById('expand_synonyms');
    if (searchFormToggle) {
        searchFormToggle.addEventListener('change', function() {
            saveSynonymPreference(this.checked);
        });
    }
}

/**
 * Add smooth transitions for search results
 */
function setupSmoothTransitions() {
    // Add fade-in animation to new search results
    document.body.addEventListener('htmx:afterSwap', function(event) {
        if (event.detail.target.id === 'search-results') {
            event.detail.target.classList.add('animate-fade-in');
        }
        if (event.detail.target.id === 'qa-response') {
            event.detail.target.classList.add('animate-fade-in');
        }
    });
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    initSearchSettings();
    setupHtmxIntegration();
    setupFormToggleSync();
    setupSmoothTransitions();
});

// Also initialize on page load (for cached pages)
window.addEventListener('load', function() {
    initSearchSettings();
});
