// @ts-check

/**
 * Navigation state management using sessionStorage.
 * Stores temporary navigation context that shouldn't pollute URLs.
 *
 * Debug mode: To enable debug logs, run in browser console:
 *   window.NavStateDebug = true
 *
 * @typedef {'recommend' | 'roles' | 'operations' | 'operation' | 'role' | 'recent' | 'analytics'} BackTarget
 *
 * @typedef {Object} NavStateData
 * @property {BackTarget} [back] - Where to navigate back to
 * @property {string} [from_role_id] - Role ID to return to
 * @property {string} [from_role_slug] - Role slug for URL
 * @property {string} [from_role_name] - Role name for display
 * @property {string} [from_operation] - Operation name to return to
 * @property {string} [ai] - AI mode flag from state
 *
 * @typedef {Object} BackDefaults
 * @property {BackTarget} [back] - Default back target
 * @property {string} [ai] - Default AI mode
 */

const NavState = {
    /** @type {string} */
    KEY: 'azurerbac_nav_state',
    /** @type {string} */
    AI_KEY: 'azurerbac_ai_mode',

    /**
     * Log debug message if debug mode is enabled.
     * @param {...any} args - Arguments to log
     * @returns {void}
     */
    _log(...args) {
        // @ts-ignore - NavStateDebug is set dynamically via browser console
        if (window.NavStateDebug) {
            console.log(...args);
        }
    },

    /**
     * Get current navigation state from sessionStorage.
     * @returns {NavStateData} Navigation state object
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
     * @param {Partial<NavStateData>} updates - Key-value pairs to update
     * @returns {void}
     */
    set(updates) {
        try {
            const current = this.get();
            /** @type {Record<string, any>} */
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
     * @returns {void}
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
            this._log('[NavState.isAiMode] AI_KEY:', this.AI_KEY, 'value:', val);
            return val === '1';
        } catch {
            return false;
        }
    },

    /**
     * Set AI mode (persists across navigation, survives clear()).
     * @param {boolean} enabled - Whether AI mode should be enabled
     */
    setAiMode(enabled) {
        this._log('[NavState.setAiMode] Called with:', enabled);
        try {
            if (enabled) {
                sessionStorage.setItem(this.AI_KEY, '1');
                this._log('[NavState.setAiMode] Set to 1, verify:', sessionStorage.getItem(this.AI_KEY));
            } else {
                sessionStorage.removeItem(this.AI_KEY);
                this._log('[NavState.setAiMode] Removed');
            }
        } catch {
            // sessionStorage not available
        }
    },

    /**
     * Initialize AI mode from URL parameter on page load.
     * If ai=1 is in URL, store it in sessionStorage.
     * Note: We don't remove ai=1 from URL as this can cause sessionStorage issues on refresh.
     * Call this on pages that support AI mode.
     * @returns {void}
     */
    initAiModeFromUrl() {
        const urlParams = new URLSearchParams(window.location.search);
        const aiParam = urlParams.get('ai');
        this._log('[NavState.initAiModeFromUrl] URL:', window.location.href, 'ai param:', aiParam);
        if (aiParam === '1') {
            this.setAiMode(true);
            // Don't remove ai=1 from URL - causes sessionStorage to be cleared on refresh in some browsers
        } else {
            this._log('[NavState.initAiModeFromUrl] No ai=1 in URL, current sessionStorage:', sessionStorage.getItem(this.AI_KEY));
        }
    },

    /**
     * Set "back" context when navigating to a detail page.
     * Call this before navigating to a role or operation detail.
     * @param {BackTarget} backTo - Where to go back to
     * @param {Partial<NavStateData>} [context] - Additional context (ai_query, from_role_id, etc.)
     * @returns {void}
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
     * @param {BackDefaults} [defaults] - Default values if not in state
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
            case 'analytics':
                url = '/analytics';
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
     * @returns {void}
     */
    consumeBackContext() {
        this.clear();
    },

    /**
     * Get label for back button based on stored state.
     * @param {BackDefaults} [defaults] - Default values if not in state
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
            case 'analytics':
                return 'Back to Analytics';
            default:
                return 'Back to Recent';
        }
    }
};

// Make available globally (browser)
if (typeof window !== 'undefined') {
    // @ts-ignore - Extending window for global access
    window.NavState = NavState;
}

// Export for testing (Node.js/Vitest)
// @ts-ignore - CommonJS export for Node.js test environment (module is Node-specific)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { NavState };
}
