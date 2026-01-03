/**
 * Navigation state management using sessionStorage.
 * Stores temporary navigation context that shouldn't pollute URLs.
 */
const NavState = {
    KEY: 'azurerbac_nav_state',
    AI_KEY: 'azurerbac_ai_mode',

    /**
     * Get current navigation state from sessionStorage.
     * @returns {Object} Navigation state object
     */
    get() {
        try {
            const stored = sessionStorage.getItem(this.KEY);
            return stored ? JSON.parse(stored) : {};
        } catch {
            return {};
        }
    },

    /**
     * Update navigation state (merges with existing).
     * @param {Object} updates - Key-value pairs to update
     */
    set(updates) {
        try {
            const current = this.get();
            const merged = { ...current, ...updates };
            // Remove null/undefined values
            Object.keys(merged).forEach(k => {
                if (merged[k] == null || merged[k] === '') {
                    delete merged[k];
                }
            });
            sessionStorage.setItem(this.KEY, JSON.stringify(merged));
        } catch {
            // sessionStorage not available
        }
    },

    /**
     * Clear all navigation state (but preserves AI mode).
     */
    clear() {
        try {
            sessionStorage.removeItem(this.KEY);
        } catch {
            // sessionStorage not available
        }
    },

    /**
     * Check if AI mode is enabled (persists across navigation within session).
     * @returns {boolean} True if AI mode is enabled
     */
    isAiMode() {
        try {
            const val = sessionStorage.getItem(this.AI_KEY);
            console.log('[NavState.isAiMode] AI_KEY:', this.AI_KEY, 'value:', val);
            return val === '1';
        } catch (e) {
            console.error('[NavState.isAiMode] Error:', e);
            return false;
        }
    },

    /**
     * Set AI mode (persists across navigation, survives clear()).
     * @param {boolean} enabled - Whether AI mode should be enabled
     */
    setAiMode(enabled) {
        console.log('[NavState.setAiMode] Called with:', enabled);
        try {
            if (enabled) {
                sessionStorage.setItem(this.AI_KEY, '1');
                console.log('[NavState.setAiMode] Set to 1, verify:', sessionStorage.getItem(this.AI_KEY));
            } else {
                sessionStorage.removeItem(this.AI_KEY);
                console.log('[NavState.setAiMode] Removed');
            }
        } catch (e) {
            console.error('[NavState.setAiMode] Error:', e);
        }
    },

    /**
     * Initialize AI mode from URL parameter on page load.
     * If ai=1 is in URL, store it in sessionStorage.
     * Note: We don't remove ai=1 from URL as this can cause sessionStorage issues on refresh.
     * Call this on pages that support AI mode.
     */
    initAiModeFromUrl() {
        const urlParams = new URLSearchParams(window.location.search);
        const aiParam = urlParams.get('ai');
        console.log('[NavState.initAiModeFromUrl] URL:', window.location.href, 'ai param:', aiParam);
        if (aiParam === '1') {
            this.setAiMode(true);
            // Don't remove ai=1 from URL - causes sessionStorage to be cleared on refresh in some browsers
        } else {
            console.log('[NavState.initAiModeFromUrl] No ai=1 in URL, current sessionStorage:', sessionStorage.getItem(this.AI_KEY));
        }
    },

    /**
     * Set "back" context when navigating to a detail page.
     * Call this before navigating to a role or operation detail.
     * @param {string} backTo - Where to go back: 'recommend', 'roles', 'recent', 'operations'
     * @param {Object} context - Additional context (ai_query, from_role_id, etc.)
     */
    setBackContext(backTo, context = {}) {
        this.set({
            back: backTo,
            ...context
        });
    },

    /**
     * Get back URL based on stored navigation state.
     * Note: This does NOT clear the state - call consumeBackContext() after reading both URL and label.
     * @param {Object} defaults - Default values if not in state
     * @returns {string} URL to navigate back to
     */
    getBackUrl(defaults = {}) {
        const state = this.get();
        const back = state.back || defaults.back || 'recent';
        const aiMode = state.ai || defaults.ai;

        let url;
        switch (back) {
            case 'recommend':
                url = '/recommend';
                break;
            case 'roles':
                url = '/roles';
                break;
            case 'operations':
                url = '/operations';
                break;
            case 'operation':
                // Back to specific operation
                if (state.from_operation) {
                    url = '/operations/' + encodeURIComponent(state.from_operation);
                } else {
                    url = '/operations';
                }
                break;
            case 'role':
                // Back to specific role
                if (state.from_role_id) {
                    url = '/roles/' + state.from_role_id;
                    if (state.from_role_slug) {
                        url += '/' + state.from_role_slug;
                    }
                } else {
                    url = '/roles';
                }
                break;
            default:
                url = '/recent';
        }

        // Add ai=1 if in AI mode (use persistent AI mode, fallback to state.ai)
        if (this.isAiMode() || aiMode) {
            url += (url.includes('?') ? '&' : '?') + 'ai=1';
        }

        return url;
    },

    /**
     * Consume (clear) the back context after using it.
     * Call this after reading both URL and label for the back button.
     */
    consumeBackContext() {
        this.clear();
    },

    /**
     * Get label for back button based on stored state.
     * @param {Object} defaults - Default values if not in state
     * @returns {string} Label for the back button
     */
    getBackLabel(defaults = {}) {
        const state = this.get();
        const back = state.back || defaults.back || 'recent';

        switch (back) {
            case 'recommend':
                return 'Back to Recommender';
            case 'roles':
                return 'Back to Roles';
            case 'operations':
                return 'Back to Operations';
            case 'operation':
                return 'Back to Operation';
            case 'role':
                return state.from_role_name
                    ? 'Back to ' + state.from_role_name
                    : 'Back to Role';
            default:
                return 'Back to Recent';
        }
    }
};

// Make available globally
window.NavState = NavState;
