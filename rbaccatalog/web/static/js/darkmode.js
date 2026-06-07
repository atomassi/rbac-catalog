// @ts-check

/**
 * Dark Mode Handler
 * - Auto-switches based on time of day (dark: 7PM-7AM)
 * - Respects system preference
 * - Allows manual toggle with localStorage persistence
 *
 * Manual control via browser console:
 *   window.darkMode.toggle()  - Toggle dark/light
 *   window.darkMode.enable()  - Force dark mode
 *   window.darkMode.disable() - Force light mode
 *   window.darkMode.auto()    - Reset to automatic
 *   window.darkMode.isDark()  - Check current state
 */

(function() {
    'use strict';

    /** @type {string} */
    const STORAGE_KEY = 'darkMode';
    /** @type {number} */
    const DARK_HOURS_START = 19; // 7 PM
    /** @type {number} */
    const DARK_HOURS_END = 7;    // 7 AM

    /**
     * Check if current time is within dark hours (7PM - 7AM)
     * @returns {boolean}
     */
    function isDarkHours() {
        const hour = new Date().getHours();
        return hour >= DARK_HOURS_START || hour < DARK_HOURS_END;
    }

    /**
     * Check if system prefers dark mode
     * @returns {boolean}
     */
    function systemPrefersDark() {
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    /**
     * Get stored preference (null if not set)
     * @returns {boolean | null}
     */
    function getStoredPreference() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored === 'true') return true;
            if (stored === 'false') return false;
            return null;
        } catch {
            return null;
        }
    }

    /**
     * Store preference
     * @param {boolean} isDark
     * @returns {void}
     */
    function setStoredPreference(isDark) {
        try {
            localStorage.setItem(STORAGE_KEY, isDark ? 'true' : 'false');
        } catch {
            // localStorage not available
        }
    }

    /**
     * Clear stored preference (revert to auto)
     * @returns {void}
     */
    function clearStoredPreference() {
        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch {
            // localStorage not available
        }
    }

    /**
     * Apply dark mode to document
     * @param {boolean} isDark
     * @returns {void}
     */
    function applyDarkMode(isDark) {
        if (isDark) {
            document.documentElement.classList.add('dark');
        } else {
            document.documentElement.classList.remove('dark');
        }
        updateToggleButton(isDark);
    }

    /**
     * Determine if dark mode should be active
     * @returns {boolean}
     */
    function shouldBeDark() {
        const stored = getStoredPreference();
        if (stored !== null) {
            return stored;
        }
        // Auto mode: prefer system setting, fallback to time-based
        if (systemPrefersDark()) {
            return true;
        }
        return isDarkHours();
    }

    /**
     * Update toggle button state
     * @param {boolean} isDark
     * @returns {void}
     */
    function updateToggleButton(isDark) {
        const toggle = document.getElementById('dark-mode-toggle');
        if (!toggle) return;

        const sunIcon = toggle.querySelector('.sun-icon');
        const moonIcon = toggle.querySelector('.moon-icon');
        const label = toggle.querySelector('.toggle-label');

        if (isDark) {
            sunIcon?.classList.remove('hidden');
            moonIcon?.classList.add('hidden');
            if (label) label.textContent = 'Light';
            toggle.setAttribute('aria-pressed', 'true');
            toggle.title = 'Switch to light mode';
        } else {
            sunIcon?.classList.add('hidden');
            moonIcon?.classList.remove('hidden');
            if (label) label.textContent = 'Dark';
            toggle.setAttribute('aria-pressed', 'false');
            toggle.title = 'Switch to dark mode';
        }
    }

    /**
     * Toggle dark mode and save preference
     * @returns {void}
     */
    function toggleDarkMode() {
        const isDark = document.documentElement.classList.contains('dark');
        const newIsDark = !isDark;
        setStoredPreference(newIsDark);
        applyDarkMode(newIsDark);
    }

    /**
     * Reset to auto mode
     * @returns {void}
     */
    function resetToAuto() {
        clearStoredPreference();
        applyDarkMode(shouldBeDark());
    }

    /**
     * Initialize dark mode
     * @returns {void}
     */
    function init() {
        // Apply immediately to prevent flash
        applyDarkMode(shouldBeDark());

        // Set up toggle button click handler
        // Check if DOM is already ready (script loaded at end of body)
        function setupToggle() {
            const toggle = document.getElementById('dark-mode-toggle');
            if (toggle) {
                toggle.addEventListener('click', toggleDarkMode);
                // Update button state
                updateToggleButton(document.documentElement.classList.contains('dark'));
            }
        }

        // If DOM is already ready, set up immediately, otherwise wait
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', setupToggle);
        } else {
            // DOM is already ready
            setupToggle();
        }

        // Listen for system preference changes
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
                // Only auto-switch if user hasn't set a preference
                if (getStoredPreference() === null) {
                    applyDarkMode(e.matches);
                }
            });
        }

        // Check time periodically (every 5 minutes) for time-based switching
        setInterval(function() {
            if (getStoredPreference() === null) {
                applyDarkMode(shouldBeDark());
            }
        }, 5 * 60 * 1000);
    }

    // Expose functions globally for manual control
    // @ts-ignore - Extending window for global access
    window.darkMode = {
        toggle: toggleDarkMode,
        enable: function() { setStoredPreference(true); applyDarkMode(true); },
        disable: function() { setStoredPreference(false); applyDarkMode(false); },
        auto: resetToAuto,
        isDark: function() { return document.documentElement.classList.contains('dark'); }
    };

    // Initialize
    init();
})();
