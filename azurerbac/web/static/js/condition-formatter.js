// @ts-check
/**
 * Condition formatter for ABAC (Attribute-Based Access Control) conditions
 * Parses and formats Azure RBAC conditions with syntax highlighting
 *
 * Supported condition patterns:
 * - ActionMatches{'action/name'} and !ActionMatches{'action/name'}
 * - ForAnyOfAnyValues:GuidEquals{guid1, guid2, ...}
 * - ForAnyOfAllValues:GuidEquals/GuidNotEquals, ForAllOfAnyValues, ForAllOfAllValues
 * - forallofanyvalues:stringlikeignorecase, stringequalsignorecase
 * - @Request[attribute] and @Resource[attribute]
 * - boolequals true/false
 * - AND/OR and &&/|| operators
 * - Negation with !(...)
 *
 * Used as an Alpine.js component: x-data="conditionFormatter(conditionText)"
 */

/** @type {Record<string, string>} HTML entity escape map */
const ESCAPE_MAP = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
};

/**
 * @typedef {Object} FormattedLine
 * @property {number} indent - Indentation level
 * @property {string} html - HTML content with syntax highlighting
 */

/**
 * @typedef {Object} Token
 * @property {'open' | 'close' | 'operator' | 'expr'} type - Token type
 * @property {string} value - Token value
 */

/**
 * @typedef {Object} ConditionFormatterComponent
 * @property {boolean} formatted - Whether to show formatted view
 * @property {FormattedLine[]} lines - Parsed and formatted lines
 * @property {() => void} init - Initialize the component
 * @property {(condition: string) => FormattedLine[]} parseCondition - Parse condition into lines
 * @property {(condition: string) => Token[]} tokenize - Tokenize condition string
 * @property {(str: string) => string} escapeHtml - Escape HTML special characters
 * @property {(text: string) => string} highlight - Apply syntax highlighting
 */

/**
 * Alpine.js component for formatting ABAC conditions
 * @param {string} rawCondition - The raw condition string to format
 * @returns {ConditionFormatterComponent} Alpine.js component data object
 */
function conditionFormatter(rawCondition) {
    return {
        formatted: true,
        /** @type {FormattedLine[]} */
        lines: [],

        init() {
            this.lines = this.parseCondition(rawCondition);
        },

        /**
         * Parse a condition string into formatted lines with indentation
         * @param {string} condition - The condition to parse
         * @returns {FormattedLine[]} Array of formatted lines
         */
        parseCondition(condition) {
            /** @type {FormattedLine[]} */
            const lines = [];
            const tokens = this.tokenize(condition);
            let indent = 0;

            for (const token of tokens) {
                switch (token.type) {
                    case 'open':
                        lines.push({ indent, html: this.highlight('(') });
                        indent++;
                        break;
                    case 'close':
                        indent = Math.max(0, indent - 1);
                        lines.push({ indent, html: this.highlight(')') });
                        break;
                    case 'operator':
                    case 'expr':
                        lines.push({ indent, html: this.highlight(token.value) });
                        break;
                }
            }

            // Fallback: if no tokens parsed, return the whole condition as-is
            return lines.length > 0
                ? lines
                : [{ indent: 0, html: this.highlight(condition) }];
        },

        /**
         * Tokenize condition into structured parts
         * Tracks depth of (), {}, and [] to avoid splitting inside them
         * 
         * Note on negation handling: This uses string inspection (endsWith('!'), includes('!('))
         * which works for known ABAC patterns but is syntax-aware, not grammar-aware.
         * Edge cases like `! (...)` or `!(A && !(B))` may not parse perfectly.
         * 
         * @param {string} condition - The condition string
         * @returns {Token[]} Array of tokens
         */
        tokenize(condition) {
            /** @type {Token[]} */
            const tokens = [];
            let i = 0;
            let currentExpr = '';
            let braceDepth = 0;    // Track {} for function args like GuidEquals{...}
            let bracketDepth = 0;  // Track [] for @Request/@Resource[...]

            /** Flush current expression to tokens if non-empty */
            const flushExpr = () => {
                const trimmed = currentExpr.trim();
                if (trimmed) {
                    tokens.push({ type: 'expr', value: trimmed });
                }
                currentExpr = '';
            };

            /** Skip whitespace from current position */
            const skipWhitespace = () => {
                while (i < condition.length && /\s/.test(condition[i])) {
                    i++;
                }
            };

            while (i < condition.length) {
                const char = condition[i];
                const remaining = condition.slice(i);

                // Track brackets [] - don't split inside
                if (char === '[') {
                    bracketDepth++;
                    currentExpr += char;
                    i++;
                    continue;
                }
                if (char === ']') {
                    bracketDepth--;
                    currentExpr += char;
                    i++;
                    continue;
                }

                // Track braces {} - don't split inside
                if (char === '{') {
                    braceDepth++;
                    currentExpr += char;
                    i++;
                    continue;
                }
                if (char === '}') {
                    braceDepth--;
                    currentExpr += char;
                    i++;
                    continue;
                }

                // If inside braces or brackets, just accumulate
                if (braceDepth > 0 || bracketDepth > 0) {
                    currentExpr += char;
                    i++;
                    continue;
                }

                // Check for AND/OR/&&/|| operators (split on them even mid-expression)
                // Use lookahead (?=...) to handle cases like AND( without trailing space
                const opMatch = remaining.match(/^\s*(AND|OR|&&|\|\|)(?=\s|\(|$)/i);
                if (opMatch) {
                    // Flush any accumulated expression before the operator
                    flushExpr();
                    // Keep original operator (preserve && and || as-is)
                    tokens.push({ type: 'operator', value: opMatch[1] });
                    i += opMatch[0].length;
                    continue;
                }

                // Opening paren
                if (char === '(') {
                    const trimmedExpr = currentExpr.trim();
                    // Check if this paren is part of !(...) negation
                    if (trimmedExpr.endsWith('!')) {
                        currentExpr += char;
                        i++;
                        continue;
                    }

                    flushExpr();
                    tokens.push({ type: 'open', value: '(' });
                    i++;
                    skipWhitespace();
                    continue;
                }

                // Closing paren
                if (char === ')') {
                    const trimmedExpr = currentExpr.trim();
                    // Check if we're closing a !(...) expression
                    if (trimmedExpr.includes('!(')) {
                        let openCount = 0;
                        for (const c of trimmedExpr) {
                            if (c === '(') openCount++;
                            if (c === ')') openCount--;
                        }
                        if (openCount > 0) {
                            // Still inside the !(...), keep accumulating
                            currentExpr += char;
                            i++;
                            continue;
                        }
                    }

                    flushExpr();
                    tokens.push({ type: 'close', value: ')' });
                    i++;
                    skipWhitespace();
                    continue;
                }

                // Regular character - accumulate
                currentExpr += char;
                i++;
            }

            flushExpr();
            return tokens;
        },

        /**
         * Escape HTML special characters to prevent XSS
         * Uses single-pass regex for efficiency with large conditions
         * @param {string} str - String to escape
         * @returns {string} Escaped string
         */
        escapeHtml(str) {
            return str.replace(/[&<>"']/g, (m) => ESCAPE_MAP[m]);
        },

        /**
         * Apply syntax highlighting to text using CSS custom properties
         * Colors are defined in input.css and adapt to light/dark mode
         * @param {string} text - Text to highlight
         * @returns {string} HTML with inline styles
         */
        highlight(text) {
            // CSS custom properties from input.css
            const blue = 'color: var(--cond-blue)';   // Functions, operators
            const green = 'color: var(--cond-green)'; // @Request/@Resource, GUIDs, booleans
            const red = 'color: var(--cond-red)';     // Strings, NOT operator
            const gray = 'color: var(--cond-gray)';   // Parentheses

            // Check for standalone operators BEFORE escaping (since && becomes &amp;&amp;)
            if (/^(AND|OR|&&|\|\|)$/i.test(text)) {
                return `<span style="${blue}; font-weight: 600">${this.escapeHtml(text)}</span>`;
            }

            // Standalone parentheses - gray (no escaping needed for these)
            if (/^[()]$/.test(text)) {
                return `<span style="${gray}">${text}</span>`;
            }

            // Escape HTML to prevent XSS (conditions come from Azure API, but defense in depth)
            let result = this.escapeHtml(text);

            // Apply highlighting patterns in order

            // 1. NOT operator at start (with or without parentheses) - red bold
            result = result.replace(
                /^(!)(ActionMatches|\()/,
                `<span style="${red}; font-weight: 600">$1</span>$2`
            );

            // 2. ActionMatches function - blue
            result = result.replace(
                /ActionMatches/g,
                `<span style="${blue}">ActionMatches</span>`
            );

            // 3. ForAnyOf/ForAllOf comparison functions (all variants) - blue
            result = result.replace(
                /(ForAnyOfAnyValues|ForAnyOfAllValues|ForAllOfAnyValues|ForAllOfAllValues):(GuidEquals|GuidNotEquals|StringEquals|StringEqualsIgnoreCase|StringLike|StringLikeIgnoreCase)/gi,
                `<span style="${blue}">$1:$2</span>`
            );

            // 4. Standalone comparison operators - blue
            result = result.replace(
                /\b(stringequalsignorecase|stringequals|boolequals)\b/gi,
                `<span style="${blue}">$1</span>`
            );

            // 5. Boolean values - green
            result = result.replace(
                /\b(true|false)\b/gi,
                `<span style="${green}">$1</span>`
            );

            // 6. @Request and @Resource keywords - green (attribute in brackets stays default)
            result = result.replace(
                /(@(?:Request|Resource))(\[[^\]]+\])/g,
                `<span style="${green}">$1</span>$2`
            );

            // 7. Strings in single quotes (action names like 'Microsoft.Authorization/...') - red
            // Note: quotes are escaped to &#39; by escapeHtml, so match that
            result = result.replace(
                /&#39;([^&]+)&#39;/g,
                `&#39;<span style="${red}">$1</span>&#39;`
            );

            // 8. GUIDs in braces - green (supports both hyphenated and non-hyphenated)
            result = result.replace(
                /\{([a-f0-9, -]+)\}/gi,
                (/** @type {string} */ _, /** @type {string} */ guids) => `{<span style="${green}">${guids}</span>}`
            );

            return result;
        }
    };
}

// Export for testing (Node.js/Vitest) while keeping browser compatibility
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { conditionFormatter, ESCAPE_MAP };
}
