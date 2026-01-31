// @ts-check
/**
 * Condition formatter for ABAC (Attribute-Based Access Control) conditions
 * Parses and formats Azure RBAC conditions with syntax highlighting
 *
 * Based on Azure ABAC condition format documentation:
 * https://learn.microsoft.com/en-us/azure/role-based-access-control/conditions-format
 *
 * NOTE: This is a best-effort pattern tokenizer, not a formal lexer with grammar rules.
 * It recognizes common ABAC patterns for highlighting and human-readable explanations.
 * Unknown or malformed input is handled gracefully (displayed as plain text).
 *
 * Recognized patterns:
 * - ActionMatches{'action/name'} and !ActionMatches{'action/name'}
 * - ForAnyOfAnyValues:GuidEquals{guid1, guid2, ...}
 * - ForAnyOfAllValues:GuidEquals/GuidNotEquals, ForAllOfAnyValues, ForAllOfAllValues
 * - forallofanyvalues:stringlikeignorecase, stringequalsignorecase
 * - @Request[attribute], @Resource[attribute], @Principal[attribute], @Environment[attribute]
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

/** Attribute keywords with their lengths for efficient lexing */
const ATTRIBUTE_KEYWORDS = ['@Request', '@Resource', '@Principal', '@Environment'];

/** Valid characters in GUID brace content (hex, comma, space, tab, hyphen) */
const GUID_CHARS = new Set('0123456789abcdefABCDEF ,\t-');

/**
 * Check if string at position matches a word (case-insensitive)
 * @param {string} text - Full text
 * @param {number} pos - Position to check
 * @param {string} word - Word to match
 * @returns {boolean}
 */
function matchesWordAt(text, pos, word) {
    if (pos + word.length > text.length) return false;
    const slice = text.slice(pos, pos + word.length);
    return slice.toLowerCase() === word.toLowerCase();
}

/**
 * Scan a condition expression for known patterns and extract tokens.
 * This is an ad-hoc pattern scanner (not a formal lexer with grammar rules).
 * It recognizes Azure ABAC syntax patterns for highlighting and analysis.
 * Unknown content is accumulated as plain 'text' tokens - no errors thrown.
 * @param {string} text - Text to scan
 * @returns {HighlightToken[]} Array of tokens with type and value
 */
function scanTokens(text) {
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
            const isGuidLike = [...inner].every(c => GUID_CHARS.has(c));
            tokens.push({ type: isGuidLike ? 'guid-brace' : 'brace', value: content });
            continue;
        }

        // Brackets with attribute path: [content]
        if (c === '[') {
            let content = '[';
            i++;
            while (i < text.length && text[i] !== ']') {
                content += text[i];
                i++;
            }
            if (i < text.length) {
                content += ']';
                i++;
            }
            tokens.push({ type: 'brace', value: content });
            continue;
        }

        // NOT operator: ! followed by non-whitespace (expression start)
        if (c === '!' && i + 1 < text.length) {
            const next = text[i + 1];
            // Recognize ! before (, A/a (ActionMatches), E/e (Exists), B/b (BoolEquals), F/f (For*), S/s (String*), G/g (Guid*)
            if (next !== ' ' && next !== '\t' && next !== '\n') {
                tokens.push({ type: 'not', value: '!' });
                i++;
                continue;
            }
        }

        // @Request, @Resource, @Principal, @Environment (handle optional space before bracket)
        if (c === '@') {
            for (const kw of ATTRIBUTE_KEYWORDS) {
                if (matchesWordAt(text, i, kw)) {
                    tokens.push({ type: 'attribute-kw', value: text.slice(i, i + kw.length) });
                    i += kw.length;
                    // Skip whitespace between attribute keyword and [ bracket
                    while (i < text.length && /\s/.test(text[i])) {
                        tokens.push({ type: 'text', value: text[i] });
                        i++;
                    }
                    break;
                }
            }
            if (tokens.length > 0 && tokens[tokens.length - 1].type === 'attribute-kw') continue;
        }

        // Keywords: ActionMatches
        if (matchesWordAt(text, i, 'ActionMatches')) {
            tokens.push({ type: 'function', value: text.slice(i, i + 13) });
            i += 13;
            continue;
        }

        // ForAnyOfAnyValues, ForAnyOfAllValues, ForAllOfAnyValues, ForAllOfAllValues with comparison
        let matchedFor = false;
        for (const prefix of FOR_PREFIXES) {
            if (matchesWordAt(text, i, prefix)) {
                // Check for : followed by comparison operator
                const afterPrefix = i + prefix.length;
                if (afterPrefix < text.length && text[afterPrefix] === ':') {
                    for (const comp of COMPARISONS) {
                        if (matchesWordAt(text, afterPrefix + 1, comp)) {
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
            if (matchesWordAt(text, i, comp)) {
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
        let matchedBool = false;
        for (const bool of ['true', 'false']) {
            if (matchesWordAt(text, i, bool)) {
                const afterPos = i + bool.length;
                if (afterPos >= text.length || !/[a-zA-Z0-9]/.test(text[afterPos])) {
                    tokens.push({ type: 'boolean', value: text.slice(i, i + bool.length) });
                    i += bool.length;
                    matchedBool = true;
                    break;
                }
            }
        }
        if (matchedBool) continue;

        // Default: accumulate as plain text
        tokens.push({ type: 'text', value: c });
        i++;
    }

    // Merge consecutive text tokens
    /** @type {HighlightToken[]} */
    const merged = [];
    for (const tok of tokens) {
        if (tok.type === 'text' && merged.length > 0 && merged[merged.length - 1].type === 'text') {
            merged[merged.length - 1].value += tok.value;
        } else {
            merged.push(tok);
        }
    }

    return merged;
}

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
 * @typedef {Object} ConditionExplanation
 * @property {string} summary - Brief one-line summary of what the condition does
 * @property {string[]} details - Detailed bullet points explaining each part
 */

/**
 * @typedef {Object} ConditionFormatterComponent
 * @property {boolean} formatted - Whether to show formatted view
 * @property {FormattedLine[]} lines - Parsed and formatted lines
 * @property {boolean} showExplanation - Whether to show explanation panel
 * @property {ConditionExplanation | null} explanation - Cached explanation
 * @property {() => void} init - Initialize the component
 * @property {() => void} toggleExplanation - Toggle explanation visibility
 * @property {() => ConditionExplanation} getExplanation - Get condition explanation
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
        showExplanation: false,
        /** @type {ConditionExplanation | null} */
        explanation: null,

        init() {
            this.lines = this.parseCondition(rawCondition);
        },

        /**
         * Toggle explanation visibility and generate explanation if needed
         */
        toggleExplanation() {
            this.showExplanation = !this.showExplanation;

            // Generate explanation using lexer-based logic if showing and not yet generated
            if (this.showExplanation && !this.explanation) {
                this.explanation = explainCondition(rawCondition);
            }
        },

        /**
         * Get the explanation for the condition (cached)
         * @returns {ConditionExplanation}
         */
        getExplanation() {
            if (!this.explanation) {
                this.explanation = explainCondition(rawCondition);
            }
            return /** @type {ConditionExplanation} */ (this.explanation);
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
         * Apply syntax highlighting using the shared lexer.
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

            // Use the shared pattern scanner
            const tokens = scanTokens(text);

            // Build HTML from tokens
            let result = '';
            for (const token of tokens) {
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
                    case 'boolean': // Intentional fall-through: booleans share styling with attribute keywords
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

/**
 * Normalize a GUID to standard hyphenated format (lowercase)
 * @param {string} guid - GUID with or without hyphens
 * @returns {string} Normalized GUID in xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx format
 */
function normalizeGuid(guid) {
    const clean = guid.replace(/[-\s]/g, '').toLowerCase();
    if (clean.length !== 32) return guid.toLowerCase();
    // Validate all characters are valid hex digits
    if (!/^[0-9a-f]{32}$/.test(clean)) return guid.toLowerCase();
    return `${clean.slice(0, 8)}-${clean.slice(8, 12)}-${clean.slice(12, 16)}-${clean.slice(16, 20)}-${clean.slice(20)}`;
}

/**
 * Extract the last segment of an attribute path for readable display
 * e.g., "Microsoft.Storage/storageAccounts/blobServices/containers:name" -> "containers name"
 * @param {string} path - The full attribute path
 * @returns {string} Human-readable attribute name
 */
function friendlyAttributeName(path) {
    // Handle tag keys with special format like "tags:Project<$key_case_sensitive$>"
    const tagMatch = path.match(/tags:([^<]+)/i);
    if (tagMatch) {
        return `tag "${tagMatch[1]}"`;
    }
    
    // Extract resource type and property from paths like "Microsoft.Storage/.../containers:name"
    const colonIdx = path.lastIndexOf(':');
    if (colonIdx !== -1) {
        const property = path.slice(colonIdx + 1);
        // Try to get the resource type before the colon
        const resourcePath = path.slice(0, colonIdx);
        const lastSlash = resourcePath.lastIndexOf('/');
        if (lastSlash !== -1) {
            const resourceType = resourcePath.slice(lastSlash + 1);
            return `${resourceType} ${property}`;
        }
        return property;
    }
    
    // Simple path - just return the last segment
    const lastSlash = path.lastIndexOf('/');
    return lastSlash !== -1 ? path.slice(lastSlash + 1) : path;
}

/**
 * Escape HTML special characters for safe insertion into HTML
 * @param {string} str - String to escape
 * @returns {string} Escaped string
 */
function escapeForHtml(str) {
    return str.replace(/[&<>"']/g, c => ESCAPE_MAP[c] || c);
}

/**
 * Wrap a value in bold tags for display in explanation details
 * @param {string} value - Value to make bold
 * @returns {string} HTML with bold tags
 */
function bold(value) {
    return `<strong>${escapeForHtml(value)}</strong>`;
}

/**
 * Join multiple values with bold formatting
 * @param {string[]} values - Values to join
 * @param {string} [separator=', '] - Separator between values
 * @returns {string} HTML with bold tags around each value
 */
function boldList(values, separator = ', ') {
    return values.map(v => bold(v)).join(separator);
}

/**
 * Wrap a value in code tags for technical identifiers (GUIDs, IDs)
 * @param {string} value - Value to format as code
 * @returns {string} HTML with code styling
 */
function code(value) {
    return `<code class="text-xs bg-blue-100 dark:bg-blue-800 px-1 py-0.5 rounded">${escapeForHtml(value)}</code>`;
}

/**
 * Join multiple values with code formatting
 * @param {string[]} values - Values to join
 * @param {string} [separator=', '] - Separator between values
 * @returns {string} HTML with code styling around each value
 */
function codeList(values, separator = ', ') {
    return values.map(v => code(v)).join(separator);
}

/**
 * Parse a condition expression and generate a human-readable explanation
 * Based on Azure ABAC condition format: https://learn.microsoft.com/en-us/azure/role-based-access-control/conditions-format
 * 
 * Condition format: !(ActionMatches{'action'}) OR (expression)
 * - If action doesn't match, condition allows the operation
 * - If action matches, the expression is evaluated to determine access
 * 
 * @param {string} condition - The ABAC condition string
 * @returns {ConditionExplanation} Summary and detailed explanation
 */
function explainCondition(condition) {
    /** @type {string[]} */
    const details = [];
    /** @type {string[]} */
    const summaryParts = [];

    // Tokenize the condition using the pattern scanner
    const tokens = scanTokens(condition);

    // Extract semantic information from tokens
    /** @type {Array<{attr: string, bracket: string}>} */
    const attrBracketPairs = [];
    /** @type {string[]} */
    const functions = [];
    /** @type {string[]} */
    const guids = [];
    /** @type {string[]} */
    const strings = [];
    /** @type {string[]} */
    const booleans = [];
    /** @type {string[]} */
    const actions = [];

    // First pass: extract all tokens and build semantic structures
    let lastAttr = null;
    for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i];
        switch (token.type) {
            case 'function':
                functions.push(token.value.toLowerCase());
                // Check if this is ActionMatches followed by a string
                if (token.value.toLowerCase() === 'actionmatches') {
                    // Look ahead for the action string in braces
                    for (let j = i + 1; j < tokens.length && j < i + 3; j++) {
                        if (tokens[j].type === 'brace' && tokens[j].value.startsWith('{')) {
                            const actionMatch = tokens[j].value.match(/'([^']+)'/);
                            if (actionMatch) {
                                actions.push(actionMatch[1]);
                            }
                            break;
                        }
                    }
                }
                break;
            case 'attribute-kw':
                lastAttr = token.value;
                break;
            case 'guid-brace': {
                // Extract GUIDs from {guid1, guid2, ...}
                const inner = token.value.slice(1, -1);
                const guidList = inner.split(',').map(g => normalizeGuid(g.trim())).filter(g => g.length === 36);
                guids.push(...guidList);
                break;
            }
            case 'brace': {
                // Extract bracket content like [Microsoft.Storage/.../containers:name]
                if (token.value.startsWith('[') && token.value.endsWith(']')) {
                    const bracketContent = token.value.slice(1, -1);
                    if (lastAttr) {
                        attrBracketPairs.push({ attr: lastAttr, bracket: bracketContent });
                        lastAttr = null;
                    }
                }
                // Extract strings from brace content like {'value1', 'value2'}
                if (token.value.startsWith('{') && token.value.endsWith('}')) {
                    const braceInner = token.value.slice(1, -1);
                    const stringMatches = braceInner.match(/'([^']+)'/g);
                    if (stringMatches) {
                        strings.push(...stringMatches.map(m => m.slice(1, -1)));
                    }
                }
                break;
            }
            case 'string':
                if (token.value.length >= 2) {
                    strings.push(token.value.slice(1, -1));
                }
                break;
            case 'boolean':
                booleans.push(token.value.toLowerCase());
                break;
        }
    }

    // Detect operator types using a helper
    // Use endsWith to avoid 'guidnotequals' matching 'guidequals' pattern
    /** @param {string} suffix */
    const hasFunction = (suffix) => functions.some(f => f.endsWith(suffix));
    const hasGuidEquals = hasFunction('guidequals') && !hasFunction('guidnotequals');
    const hasGuidNotEquals = hasFunction('guidnotequals');
    const hasStringEquals = hasFunction('stringequals');
    const hasStringLike = hasFunction('stringlike');
    const hasActionMatches = functions.includes('actionmatches');
    const hasSubOperationMatches = functions.includes('suboperationmatches');

    // Categorize attributes by source
    /** @param {string} source */
    const getAttrs = (source) => attrBracketPairs.filter(p => p.attr === source);
    const requestAttrs = getAttrs('@Request');
    const resourceAttrs = getAttrs('@Resource');
    const principalAttrs = getAttrs('@Principal');
    const environmentAttrs = getAttrs('@Environment');

    // Helper to find attribute matching a pattern
    /** 
     * @param {Array<{attr: string, bracket: string}>} attrs 
     * @param {string} pattern 
     */
    const findAttr = (attrs, pattern) => attrs.find(p => p.bracket.toLowerCase().includes(pattern));

    // =====================================================================
    // 1. ACTION RESTRICTIONS (ActionMatches patterns)
    // =====================================================================
    if (hasActionMatches && actions.length > 0) {
        const actionList = actions.map(a => {
            // Shorten the action for display
            const parts = a.split('/');
            return parts.length > 3 ? `.../${parts.slice(-2).join('/')}` : a;
        });
        if (actions.length === 1) {
            details.push(`Applies only when performing action: ${bold(actionList[0])}`);
        } else {
            details.push(`Applies only when performing actions: ${boldList(actionList)}`);
        }
        summaryParts.push('action-specific restriction');
    }

    // =====================================================================
    // 2. STORAGE CONDITIONS (Blob/Container access)
    // =====================================================================
    
    // Container name restriction
    const containerNameAttr = findAttr(resourceAttrs, 'containers:name') || findAttr(resourceAttrs, 'containers/name');
    if (containerNameAttr && strings.length > 0) {
        const containerNames = strings.filter(s => !s.includes('/') && !s.includes('*'));
        if (containerNames.length > 0) {
            const verb = hasStringLike ? 'match pattern' : 'be';
            details.push(`Container name must ${verb}: ${boldList(containerNames, ' or ')}`);
            summaryParts.push('restricts container access');
        }
    }

    // Blob path/prefix restriction
    const blobPathAttr = findAttr(resourceAttrs, 'blobs:path') || findAttr(resourceAttrs, 'blobs/path');
    if (blobPathAttr && strings.length > 0) {
        const pathPatterns = strings.filter(s => s.includes('/') || s.includes('*'));
        if (pathPatterns.length > 0) {
            details.push(`Blob path must match: ${boldList(pathPatterns, ' or ')}`);
            summaryParts.push('restricts blob paths');
        }
    }

    // Blob index tags
    const tagAttr = findAttr(resourceAttrs, 'tags:') || findAttr(requestAttrs, 'tags:');
    if (tagAttr) {
        const tagName = friendlyAttributeName(tagAttr.bracket);
        if (strings.length > 0) {
            const tagValues = strings.slice(0, 3);
            const verb = hasStringLike ? 'match pattern' : 'equal';
            details.push(`${bold(tagName)} must ${verb}: ${boldList(tagValues, ' or ')}${strings.length > 3 ? '...' : ''}`);
            summaryParts.push('restricts by blob tags');
        }
    }

    // Encryption scope
    if (findAttr(resourceAttrs, 'encryptionscopes:name') && strings.length > 0) {
        details.push(`Encryption scope must be: ${boldList(strings, ' or ')}`);
        summaryParts.push('restricts encryption scope');
    }

    // Hierarchical namespace (HNS) enabled
    if (findAttr(resourceAttrs, 'ishnsenabled') && booleans.length > 0) {
        const hnsRequired = booleans.includes('true');
        details.push(hnsRequired 
            ? 'Storage account must have hierarchical namespace enabled (Data Lake Gen2)'
            : 'Storage account must NOT have hierarchical namespace enabled');
        summaryParts.push('restricts by storage account type');
    }

    // =====================================================================
    // 3. ENVIRONMENT CONDITIONS
    // =====================================================================
    
    // Private link requirement
    if (findAttr(environmentAttrs, 'isprivatelink') && booleans.includes('true')) {
        details.push('Access must be over a private link connection');
        summaryParts.push('requires private link');
    }

    // Private endpoint restriction
    if (findAttr(environmentAttrs, 'privateendpoints') && strings.length > 0) {
        details.push(`Access restricted to private endpoint(s): ${boldList(strings)}`);
        summaryParts.push('restricts by private endpoint');
    }

    // Subnet restriction
    if (findAttr(environmentAttrs, 'subnets') && strings.length > 0) {
        details.push(`Access restricted to subnet(s): ${boldList(strings)}`);
        summaryParts.push('restricts by network');
    }

    // Time-based conditions (UTC now)
    if (findAttr(environmentAttrs, 'utcnow')) {
        details.push('Access is restricted based on current date/time');
        summaryParts.push('time-based restriction');
    }

    // =====================================================================
    // 4. PRINCIPAL CONDITIONS (Custom Security Attributes)
    // =====================================================================
    if (principalAttrs.length > 0) {
        details.push('Evaluates custom security attributes assigned to the requesting principal (user or service principal)');
        summaryParts.push('restricts by principal attributes');
    }

    // =====================================================================
    // 5. ROLE ASSIGNMENT CONDITIONS (Microsoft.Authorization)
    // =====================================================================
    
    // Helper to check if any attr matches a pattern
    /** 
     * @param {Array<{attr: string, bracket: string}>} attrs 
     * @param {string} pattern 
     */
    const hasAttr = (attrs, pattern) => attrs.some(p => p.bracket.toLowerCase().includes(pattern));
    
    const requestHasRoleDefId = hasAttr(requestAttrs, 'roledefinitionid');
    const resourceHasRoleDefId = hasAttr(resourceAttrs, 'roledefinitionid');

    // OBO token requirement
    if (resourceAttrs.some(p => p.bracket.toLowerCase() === 'hasobotoken') && booleans.includes('true')) {
        details.push('Requires On-Behalf-Of (OBO) token — request must be made on behalf of a signed-in user');
        summaryParts.push('requires user delegation');
    }

    // Role definition restrictions
    if (guids.length > 0 && (requestHasRoleDefId || resourceHasRoleDefId)) {
        const guidDisplay = codeList(guids);
        
        if (requestHasRoleDefId) {
            const verb = hasGuidNotEquals ? 'Cannot assign' : 'Can only assign';
            const summaryText = hasGuidNotEquals ? 'restricts role assignment creation' : 'restricts assignable roles';
            details.push(`${verb} role definitions: ${guidDisplay}`);
            summaryParts.push(summaryText);
        }
        
        if (resourceHasRoleDefId) {
            const verb = hasGuidNotEquals ? 'Cannot delete' : 'Can only delete';
            const summaryText = hasGuidNotEquals ? 'restricts role assignment deletion' : 'restricts removable roles';
            details.push(`${verb} role assignments where role definition ID is: ${guidDisplay}`);
            summaryParts.push(summaryText);
        }

        if (requestHasRoleDefId && resourceHasRoleDefId) {
            // Constrained delegation - replace summary parts
            summaryParts.length = 0;
            summaryParts.push('constrained role delegation');
        }
    }

    // Principal type restriction
    const principalTypeAttr = findAttr(requestAttrs, 'principaltype') || findAttr(resourceAttrs, 'principaltype');
    if (principalTypeAttr && strings.length > 0) {
        const principalTypes = strings.filter(s => 
            ['serviceprincipal', 'user', 'group', 'foreigngroup'].includes(s.toLowerCase())
        );
        if (principalTypes.length > 0) {
            details.push(`Principal type must be: ${boldList(principalTypes, ' or ')}`);
            summaryParts.push('restricts principal type');
        }
    }

    // =====================================================================
    // GENERATE SUMMARY
    // =====================================================================
    let summary = '';
    if (summaryParts.length > 0) {
        const uniqueParts = [...new Set(summaryParts)];
        summary = uniqueParts.map((p, i) => i === 0 ? p.charAt(0).toUpperCase() + p.slice(1) : p).join(', ');
    } else {
        summary = 'ABAC condition';
    }

    // =====================================================================
    // FALLBACK - Generic description if no specific patterns matched
    // =====================================================================
    if (details.length === 0) {
        details.push('This condition uses Azure Attribute-Based Access Control (ABAC) to provide fine-grained access control');
        
        // Describe what attribute sources are used
        if (requestAttrs.length > 0) {
            details.push('Evaluates attributes of the incoming request (e.g., requested tags, properties)');
        }
        if (resourceAttrs.length > 0) {
            details.push('Evaluates attributes of the target resource (e.g., existing tags, container name)');
        }
        if (principalAttrs.length > 0) {
            details.push('Evaluates custom security attributes of the requesting principal');
        }
        if (environmentAttrs.length > 0) {
            details.push('Evaluates environment conditions (e.g., network, time)');
        }
        if (hasActionMatches) {
            details.push('Applies only to specific actions');
        }
        if (hasSubOperationMatches) {
            details.push('Distinguishes between sub-operations (e.g., read vs list)');
        }
        if (guids.length > 0) {
            details.push(`References ${guids.length} GUID value(s)`);
        }
        if (strings.length > 0 && strings.length <= 5) {
            details.push(`Compares against values: ${strings.join(', ')}`);
        } else if (strings.length > 5) {
            details.push(`Compares against ${strings.length} string values`);
        }
    }

    return { summary, details };
}

// Export for testing (Node.js/Vitest) while keeping browser compatibility
// @ts-ignore - CommonJS export for Node.js test environment (module is Node-specific)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { conditionFormatter, explainCondition, normalizeGuid, friendlyAttributeName, scanTokens, matchesWordAt, ESCAPE_MAP };
}
