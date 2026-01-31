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

/** Quantifier prefixes for ABAC conditions (ForAnyOf*, ForAllOf*) */
const FOR_PREFIXES = ['ForAnyOfAnyValues', 'ForAnyOfAllValues', 'ForAllOfAnyValues', 'ForAllOfAllValues'];

/** Comparison operators (case-insensitive matching via matchesWordAt) */
const COMPARISONS = ['GuidEquals', 'GuidNotEquals', 'StringEquals', 'StringEqualsIgnoreCase', 'StringLike', 'StringLikeIgnoreCase', 'BoolEquals'];

/** Valid characters in GUID brace content (hex, comma, space, tab, hyphen) */
const GUID_CHARS = new Set('0123456789abcdefABCDEF ,\t-');

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
 * @typedef {'string' | 'guid-brace' | 'brace' | 'not' | 'function' | 'boolean' | 'attribute-kw' | 'text'} HighlightTokenType
 */

/**
 * @typedef {Object} HighlightToken
 * @property {HighlightTokenType} type - Semantic token type for syntax highlighting
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
 * @property {(text: string, pos: number, word: string) => boolean} matchesWordAt - Check if word matches at position
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
         * @param {string} str - String to escape
         * @returns {string} Escaped string
         */
        escapeHtml(str) {
            let result = '';
            for (let i = 0; i < str.length; i++) {
                const c = str[i];
                result += ESCAPE_MAP[c] || c;
            }
            return result;
        },

        /**
         * Check if string at position matches a word (case-insensitive)
         * @param {string} text - Full text
         * @param {number} pos - Position to check
         * @param {string} word - Word to match
         * @returns {boolean}
         */
        matchesWordAt(text, pos, word) {
            if (pos + word.length > text.length) return false;
            const slice = text.slice(pos, pos + word.length);
            return slice.toLowerCase() === word.toLowerCase();
        },

        /**
         * Apply syntax highlighting using a character-by-character lexer.
         * @param {string} text - Text to highlight
         * @returns {string} HTML with inline styles
         */
        highlight(text) {
            // CSS custom properties from input.css
            const blue = 'color: var(--cond-blue)';   // Functions, operators
            const green = 'color: var(--cond-green)'; // @Request/@Resource, GUIDs, booleans
            const red = 'color: var(--cond-red)';     // Strings, NOT operator
            const gray = 'color: var(--cond-gray)';   // Parentheses

            // Handle standalone operators
            const upper = text.toUpperCase();
            if (upper === 'AND' || upper === 'OR' || text === '&&' || text === '||') {
                return `<span style="${blue}; font-weight: 600">${this.escapeHtml(text)}</span>`;
            }

            // Standalone parentheses
            if (text === '(' || text === ')') {
                return `<span style="${gray}">${text}</span>`;
            }

            /** @type {HighlightToken[]} */
            const tokens = [];
            let i = 0;

            while (i < text.length) {
                const c = text[i];

                // Single-quoted string: 'content'
                if (c === "'") {
                    let str = "'";
                    i++;
                    while (i < text.length && text[i] !== "'") {
                        str += text[i];
                        i++;
                    }
                    if (i < text.length) {
                        str += "'";
                        i++;
                    }
                    tokens.push({ type: 'string', value: str });
                    continue;
                }

                // Braces with GUIDs/content: {content}
                if (c === '{') {
                    let content = '{';
                    i++;
                    while (i < text.length && text[i] !== '}') {
                        content += text[i];
                        i++;
                    }
                    if (i < text.length) {
                        content += '}';
                        i++;
                    }
                    // Check if content looks like GUIDs (hex chars, commas, spaces, hyphens)
                    const inner = content.slice(1, -1);
                    let isGuidLike = true;
                    for (let j = 0; j < inner.length; j++) {
                        if (!GUID_CHARS.has(inner[j])) {
                            isGuidLike = false;
                            break;
                        }
                    }
                    tokens.push({ type: isGuidLike ? 'guid-brace' : 'brace', value: content });
                    continue;
                }

                // NOT operator: ! followed by ( or A (ActionMatches)
                if (c === '!' && i + 1 < text.length) {
                    const next = text[i + 1];
                    if (next === '(' || next === 'A' || next === 'a') {
                        tokens.push({ type: 'not', value: '!' });
                        i++;
                        continue;
                    }
                }

                // @Request or @Resource (handle optional space before bracket)
                if (c === '@') {
                    const isReq = this.matchesWordAt(text, i, '@Request');
                    const isRes = this.matchesWordAt(text, i, '@Resource');
                    if (isReq || isRes) {
                        const len = isReq ? 8 : 9;
                        tokens.push({ type: 'attribute-kw', value: text.slice(i, i + len) });
                        i += len;
                        // Skip whitespace between @Request/@Resource and [ bracket
                        while (i < text.length && /\s/.test(text[i])) {
                            tokens.push({ type: 'text', value: text[i] });
                            i++;
                        }
                        continue;
                    }
                }

                // Keywords: ActionMatches
                if (this.matchesWordAt(text, i, 'ActionMatches')) {
                    tokens.push({ type: 'function', value: text.slice(i, i + 13) });
                    i += 13;
                    continue;
                }

                // ForAnyOfAnyValues, ForAnyOfAllValues, ForAllOfAnyValues, ForAllOfAllValues with comparison
                let matchedFor = false;
                for (const prefix of FOR_PREFIXES) {
                    if (this.matchesWordAt(text, i, prefix)) {
                        // Check for : followed by comparison operator
                        const afterPrefix = i + prefix.length;
                        if (afterPrefix < text.length && text[afterPrefix] === ':') {
                            for (const comp of COMPARISONS) {
                                if (this.matchesWordAt(text, afterPrefix + 1, comp)) {
                                    const fullLen = prefix.length + 1 + comp.length;
                                    tokens.push({ type: 'function', value: text.slice(i, i + fullLen) });
                                    i += fullLen;
                                    matchedFor = true;
                                    break;
                                }
                            }
                            // If we matched prefix and colon but no comparison, still consume the prefix:colon part
                            if (!matchedFor) {
                                tokens.push({ type: 'function', value: text.slice(i, afterPrefix + 1) });
                                i = afterPrefix + 1;
                                matchedFor = true;
                            }
                        } else {
                            // Just the prefix without colon - consume it as function
                            tokens.push({ type: 'function', value: text.slice(i, afterPrefix) });
                            i = afterPrefix;
                            matchedFor = true;
                        }
                        break;
                    }
                }
                if (matchedFor) continue;

                // Standalone comparisons (same operators, just not prefixed with ForXxxOfXxxValues:)
                let matchedComp = false;
                for (const comp of COMPARISONS) {
                    if (this.matchesWordAt(text, i, comp)) {
                        // Check it's a word boundary (not followed by alphanumeric)
                        const afterPos = i + comp.length;
                        if (afterPos >= text.length || !/[a-zA-Z0-9]/.test(text[afterPos])) {
                            tokens.push({ type: 'function', value: text.slice(i, i + comp.length) });
                            i += comp.length;
                            matchedComp = true;
                            break;
                        }
                    }
                }
                if (matchedComp) continue;

                // Boolean values: true, false
                if (this.matchesWordAt(text, i, 'true')) {
                    const afterPos = i + 4;
                    if (afterPos >= text.length || !/[a-zA-Z0-9]/.test(text[afterPos])) {
                        tokens.push({ type: 'boolean', value: text.slice(i, i + 4) });
                        i += 4;
                        continue;
                    }
                }
                if (this.matchesWordAt(text, i, 'false')) {
                    const afterPos = i + 5;
                    if (afterPos >= text.length || !/[a-zA-Z0-9]/.test(text[afterPos])) {
                        tokens.push({ type: 'boolean', value: text.slice(i, i + 5) });
                        i += 5;
                        continue;
                    }
                }

                // Default: accumulate as plain text
                tokens.push({ type: 'text', value: c });
                i++;
            }

            // Merge consecutive text tokens
            /** @type {Array<{type: string, value: string}>} */
            const merged = [];
            for (const tok of tokens) {
                if (tok.type === 'text' && merged.length > 0 && merged[merged.length - 1].type === 'text') {
                    merged[merged.length - 1].value += tok.value;
                } else {
                    merged.push(tok);
                }
            }

            // Build HTML from tokens
            let result = '';
            for (const token of merged) {
                switch (token.type) {
                    case 'string':
                        // 'content' -> '&#39;<span>content</span>&#39;'
                        if (token.value.length >= 2 && token.value[0] === "'" && token.value[token.value.length - 1] === "'") {
                            const inner = token.value.slice(1, -1);
                            result += `&#39;<span style="${red}">${this.escapeHtml(inner)}</span>&#39;`;
                        } else {
                            result += this.escapeHtml(token.value);
                        }
                        break;
                    case 'guid-brace':
                        // {guids} -> {<span>guids</span>}
                        if (token.value.length >= 2) {
                            const inner = token.value.slice(1, -1);
                            result += `{<span style="${green}">${this.escapeHtml(inner)}</span>}`;
                        } else {
                            result += this.escapeHtml(token.value);
                        }
                        break;
                    case 'brace':
                        result += this.escapeHtml(token.value);
                        break;
                    case 'not':
                        result += `<span style="${red}; font-weight: 600">${this.escapeHtml(token.value)}</span>`;
                        break;
                    case 'function':
                        result += `<span style="${blue}">${this.escapeHtml(token.value)}</span>`;
                        break;
                    case 'boolean':
                        result += `<span style="${green}">${this.escapeHtml(token.value)}</span>`;
                        break;
                    case 'attribute-kw':
                        result += `<span style="${green}">${this.escapeHtml(token.value)}</span>`;
                        break;
                    default:
                        result += this.escapeHtml(token.value);
                }
            }

            return result;
        }
    };
}

// Export for testing (Node.js/Vitest) while keeping browser compatibility
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { conditionFormatter, ESCAPE_MAP };
}
