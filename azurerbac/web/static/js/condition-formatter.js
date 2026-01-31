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
 * Alpine.js component: x-data="conditionFormatter(conditionText)"
 */

// =============================================================================
// Constants
// =============================================================================

/** @type {Record<string, string>} */
const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const FOR_PREFIXES = ['ForAnyOfAnyValues', 'ForAnyOfAllValues', 'ForAllOfAnyValues', 'ForAllOfAllValues'];
const COMPARISONS = ['GuidEquals', 'GuidNotEquals', 'StringEquals', 'StringEqualsIgnoreCase', 'StringLike', 'StringLikeIgnoreCase', 'BoolEquals'];
const ATTRIBUTE_KEYWORDS = ['@Request', '@Resource', '@Principal', '@Environment'];
const GUID_CHARS = new Set('0123456789abcdefABCDEF ,\t-');

const STYLES = {
    blue: 'color: var(--cond-blue)',
    green: 'color: var(--cond-green)',
    red: 'color: var(--cond-red)',
    gray: 'color: var(--cond-gray)'
};

const OPERATORS = new Set(['AND', 'OR', '&&', '||']);

// =============================================================================
// Type Definitions
// =============================================================================

/** @typedef {{indent: number, html: string}} FormattedLine */
/** @typedef {{type: 'open' | 'close' | 'operator' | 'expr', value: string}} StructureToken */
/** @typedef {'string' | 'guid-brace' | 'brace' | 'not' | 'function' | 'boolean' | 'attribute-kw' | 'text'} HighlightTokenType */
/** @typedef {{type: HighlightTokenType, value: string}} HighlightToken */
/** @typedef {{summary: string, details: string[]}} ConditionExplanation */
/** @typedef {{attr: string, bracket: string}} AttrBracketPair */

// =============================================================================
// HTML Utilities
// =============================================================================

/** @param {string} str */
function escapeHtml(str) {
    let result = '';
    for (const c of str) {
        result += ESCAPE_MAP[c] || c;
    }
    return result;
}

/** @param {string} value */
function bold(value) {
    return `<strong>${escapeHtml(value)}</strong>`;
}

/** @param {string[]} values @param {string} [sep=', '] */
function boldList(values, sep = ', ') {
    return values.map(bold).join(sep);
}

/** @param {string} value */
function code(value) {
    return `<code class="text-xs bg-blue-100 dark:bg-blue-800 px-1 py-0.5 rounded">${escapeHtml(value)}</code>`;
}

/** @param {string[]} values @param {string} [sep=', '] */
function codeList(values, sep = ', ') {
    return values.map(code).join(sep);
}

// =============================================================================
// Lexer - Pattern Scanner
// =============================================================================

/** @param {string} text @param {number} pos @param {string} word */
function matchesWordAt(text, pos, word) {
    if (pos + word.length > text.length) return false;
    return text.slice(pos, pos + word.length).toLowerCase() === word.toLowerCase();
}

/** @param {string} text @param {number} pos @param {string[]} words @returns {string|null} */
function matchAnyWordAt(text, pos, words) {
    for (const word of words) {
        if (matchesWordAt(text, pos, word)) {
            const afterPos = pos + word.length;
            if (afterPos >= text.length || !/[a-zA-Z0-9]/.test(text[afterPos])) {
                return text.slice(pos, pos + word.length);
            }
        }
    }
    return null;
}

/** @param {string} text @param {number} start @param {string} closeChar @returns {[string, number]} */
function scanUntil(text, start, closeChar) {
    let content = text[start];
    let i = start + 1;
    while (i < text.length && text[i] !== closeChar) {
        content += text[i++];
    }
    if (i < text.length) {
        content += text[i++];
    }
    return [content, i];
}

/**
 * Scan condition for highlight tokens
 * @param {string} text
 * @returns {HighlightToken[]}
 */
function scanTokens(text) {
    /** @type {HighlightToken[]} */
    const tokens = [];
    let i = 0;

    while (i < text.length) {
        const c = text[i];

        // Single-quoted string
        if (c === "'") {
            const [str, nextI] = scanUntil(text, i, "'");
            tokens.push({ type: 'string', value: str });
            i = nextI;
            continue;
        }

        // Braces {content}
        if (c === '{') {
            const [content, nextI] = scanUntil(text, i, '}');
            // Only treat as GUID-like if properly closed and has content
            const isClosed = content.endsWith('}');
            const inner = isClosed ? content.slice(1, -1) : '';
            const isGuidLike = isClosed && inner.length > 0 && [...inner].every(ch => GUID_CHARS.has(ch));
            tokens.push({ type: isGuidLike ? 'guid-brace' : 'brace', value: content });
            i = nextI;
            continue;
        }

        // Brackets [content]
        if (c === '[') {
            const [content, nextI] = scanUntil(text, i, ']');
            tokens.push({ type: 'brace', value: content });
            i = nextI;
            continue;
        }

        // NOT operator
        if (c === '!' && i + 1 < text.length && !/\s/.test(text[i + 1])) {
            tokens.push({ type: 'not', value: '!' });
            i++;
            continue;
        }

        // Attribute keywords (@Request, @Resource, etc.)
        if (c === '@') {
            const kw = ATTRIBUTE_KEYWORDS.find(k => matchesWordAt(text, i, k));
            if (kw) {
                tokens.push({ type: 'attribute-kw', value: text.slice(i, i + kw.length) });
                i += kw.length;
                while (i < text.length && /\s/.test(text[i])) {
                    tokens.push({ type: 'text', value: text[i++] });
                }
                continue;
            }
        }

        // ActionMatches
        if (matchesWordAt(text, i, 'ActionMatches')) {
            tokens.push({ type: 'function', value: text.slice(i, i + 13) });
            i += 13;
            continue;
        }

        // ForXxxOfXxxValues:Comparison patterns
        let matchedFor = false;
        for (const prefix of FOR_PREFIXES) {
            if (!matchesWordAt(text, i, prefix)) continue;

            const afterPrefix = i + prefix.length;
            if (afterPrefix < text.length && text[afterPrefix] === ':') {
                const comp = COMPARISONS.find(cmp => matchesWordAt(text, afterPrefix + 1, cmp));
                if (comp) {
                    const fullLen = prefix.length + 1 + comp.length;
                    tokens.push({ type: 'function', value: text.slice(i, i + fullLen) });
                    i += fullLen;
                } else {
                    tokens.push({ type: 'function', value: text.slice(i, afterPrefix + 1) });
                    i = afterPrefix + 1;
                }
            } else {
                tokens.push({ type: 'function', value: text.slice(i, afterPrefix) });
                i = afterPrefix;
            }
            matchedFor = true;
            break;
        }
        if (matchedFor) continue;

        // Standalone comparisons
        const compMatch = matchAnyWordAt(text, i, COMPARISONS);
        if (compMatch) {
            tokens.push({ type: 'function', value: compMatch });
            i += compMatch.length;
            continue;
        }

        // Boolean values
        const boolMatch = matchAnyWordAt(text, i, ['true', 'false']);
        if (boolMatch) {
            tokens.push({ type: 'boolean', value: boolMatch });
            i += boolMatch.length;
            continue;
        }

        // Default: plain text
        tokens.push({ type: 'text', value: c });
        i++;
    }

    // Merge consecutive text tokens
    return tokens.reduce((merged, tok) => {
        const last = merged[merged.length - 1];
        if (tok.type === 'text' && last?.type === 'text') {
            last.value += tok.value;
        } else {
            merged.push(tok);
        }
        return merged;
    }, /** @type {HighlightToken[]} */([]));
}

// =============================================================================
// Syntax Highlighting
// =============================================================================

/** @param {HighlightToken} token */
function renderToken(token) {
    const { type, value } = token;

    switch (type) {
        case 'string':
            if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
                return `&#39;<span style="${STYLES.red}">${escapeHtml(value.slice(1, -1))}</span>&#39;`;
            }
            return escapeHtml(value);

        case 'guid-brace':
            if (value.length >= 2) {
                return `{<span style="${STYLES.green}">${escapeHtml(value.slice(1, -1))}</span>}`;
            }
            return escapeHtml(value);

        case 'not':
            return `<span style="${STYLES.red}; font-weight: 600">${escapeHtml(value)}</span>`;

        case 'function':
            return `<span style="${STYLES.blue}">${escapeHtml(value)}</span>`;

        case 'boolean':
        case 'attribute-kw':
            return `<span style="${STYLES.green}">${escapeHtml(value)}</span>`;

        default:
            return escapeHtml(value);
    }
}

/** @param {string} text */
function highlight(text) {
    if (OPERATORS.has(text) || OPERATORS.has(text.toUpperCase())) {
        return `<span style="${STYLES.blue}; font-weight: 600">${escapeHtml(text)}</span>`;
    }
    if (text === '(' || text === ')') {
        return `<span style="${STYLES.gray}">${text}</span>`;
    }
    return scanTokens(text).map(renderToken).join('');
}

// =============================================================================
// Structure Tokenizer (for indentation)
// =============================================================================

/** @param {string} condition @returns {StructureToken[]} */
function tokenizeStructure(condition) {
    /** @type {StructureToken[]} */
    const tokens = [];
    let i = 0;
    let currentExpr = '';
    let braceDepth = 0;
    let bracketDepth = 0;

    const flush = () => {
        const trimmed = currentExpr.trim();
        if (trimmed) tokens.push({ type: 'expr', value: trimmed });
        currentExpr = '';
    };

    const skipWs = () => {
        while (i < condition.length && /\s/.test(condition[i])) i++;
    };

    while (i < condition.length) {
        const char = condition[i];

        // Track depth - don't split inside brackets/braces
        if (char === '[') { bracketDepth++; currentExpr += char; i++; continue; }
        if (char === ']') { bracketDepth--; currentExpr += char; i++; continue; }
        if (char === '{') { braceDepth++; currentExpr += char; i++; continue; }
        if (char === '}') { braceDepth--; currentExpr += char; i++; continue; }

        if (braceDepth > 0 || bracketDepth > 0) {
            currentExpr += char;
            i++;
            continue;
        }

        // Operators (AND/OR/&&/||)
        const opMatch = condition.slice(i).match(/^\s*(AND|OR|&&|\|\|)(?=\s|\(|$)/i);
        if (opMatch) {
            flush();
            tokens.push({ type: 'operator', value: opMatch[1] });
            i += opMatch[0].length;
            continue;
        }

        // Parentheses
        if (char === '(') {
            if (currentExpr.trim().endsWith('!')) {
                currentExpr += char;
                i++;
                continue;
            }
            flush();
            tokens.push({ type: 'open', value: '(' });
            i++;
            skipWs();
            continue;
        }

        if (char === ')') {
            const trimmed = currentExpr.trim();
            if (trimmed.includes('!(')) {
                let openCount = 0;
                for (const c of trimmed) {
                    if (c === '(') openCount++;
                    if (c === ')') openCount--;
                }
                if (openCount > 0) {
                    currentExpr += char;
                    i++;
                    continue;
                }
            }
            flush();
            tokens.push({ type: 'close', value: ')' });
            i++;
            skipWs();
            continue;
        }

        currentExpr += char;
        i++;
    }

    flush();
    return tokens;
}

// =============================================================================
// GUID & Attribute Helpers
// =============================================================================

/** @param {string} guid */
function normalizeGuid(guid) {
    const clean = guid.replace(/[-\s]/g, '').toLowerCase();
    if (clean.length !== 32 || !/^[0-9a-f]{32}$/.test(clean)) {
        return guid.toLowerCase();
    }
    return `${clean.slice(0, 8)}-${clean.slice(8, 12)}-${clean.slice(12, 16)}-${clean.slice(16, 20)}-${clean.slice(20)}`;
}

/** @param {string} path */
function friendlyAttributeName(path) {
    const tagMatch = path.match(/tags:([^<]+)/i);
    if (tagMatch) return `tag "${tagMatch[1]}"`;

    const colonIdx = path.lastIndexOf(':');
    if (colonIdx !== -1) {
        const property = path.slice(colonIdx + 1);
        const resourcePath = path.slice(0, colonIdx);
        const lastSlash = resourcePath.lastIndexOf('/');
        if (lastSlash !== -1) {
            return `${resourcePath.slice(lastSlash + 1)} ${property}`;
        }
        return property;
    }

    const lastSlash = path.lastIndexOf('/');
    return lastSlash !== -1 ? path.slice(lastSlash + 1) : path;
}

// =============================================================================
// Condition Explainer
// =============================================================================

/**
 * Extract semantic data from tokens
 * @param {HighlightToken[]} tokens
 */
function extractSemanticData(tokens) {
    /** @type {AttrBracketPair[]} */
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
    /** @type {string|null} */
    let lastAttr = null;

    for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i];

        switch (token.type) {
            case 'function':
                functions.push(token.value.toLowerCase());
                if (token.value.toLowerCase() === 'actionmatches') {
                    for (let j = i + 1; j < tokens.length && j < i + 3; j++) {
                        if (tokens[j].type === 'brace' && tokens[j].value.startsWith('{')) {
                            const actionMatch = tokens[j].value.match(/'([^']+)'/);
                            if (actionMatch) actions.push(actionMatch[1]);
                            break;
                        }
                    }
                }
                break;

            case 'attribute-kw':
                lastAttr = token.value;
                break;

            case 'guid-brace': {
                const inner = token.value.slice(1, -1);
                const guidList = inner.split(',').map(g => normalizeGuid(g.trim())).filter(g => g.length === 36);
                guids.push(...guidList);
                break;
            }

            case 'brace': {
                if (token.value.startsWith('[') && token.value.endsWith(']')) {
                    const bracketContent = token.value.slice(1, -1);
                    if (lastAttr) {
                        attrBracketPairs.push({ attr: lastAttr, bracket: bracketContent });
                        lastAttr = null;
                    }
                }
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
                if (token.value.length >= 2) strings.push(token.value.slice(1, -1));
                break;

            case 'boolean':
                booleans.push(token.value.toLowerCase());
                break;
        }
    }

    return { attrBracketPairs, functions, guids, strings, booleans, actions };
}

/**
 * Generate human-readable explanation for ABAC condition
 * @param {string} condition
 * @returns {ConditionExplanation}
 */
function explainCondition(condition) {
    const tokens = scanTokens(condition);
    const { attrBracketPairs, functions, guids, strings, booleans, actions } = extractSemanticData(tokens);

    /** @type {string[]} */
    const details = [];
    /** @type {string[]} */
    const summaryParts = [];

    // Helper functions
    /** @param {string} suffix */
    const hasFunction = (suffix) => functions.some(f => f.endsWith(suffix));
    const hasGuidNotEquals = hasFunction('guidnotequals');
    const hasStringLike = hasFunction('stringlike');
    const hasActionMatches = functions.includes('actionmatches');
    const hasSubOperationMatches = functions.includes('suboperationmatches');

    /** @param {string} source */
    const getAttrs = (source) => attrBracketPairs.filter(p => p.attr === source);
    const requestAttrs = getAttrs('@Request');
    const resourceAttrs = getAttrs('@Resource');
    const principalAttrs = getAttrs('@Principal');
    const environmentAttrs = getAttrs('@Environment');

    /** @param {AttrBracketPair[]} attrs @param {string} pattern */
    const findAttr = (attrs, pattern) => attrs.find(p => p.bracket.toLowerCase().includes(pattern));

    /** @param {AttrBracketPair[]} attrs @param {string} pattern */
    const hasAttr = (attrs, pattern) => attrs.some(p => p.bracket.toLowerCase().includes(pattern));

    // 1. ACTION RESTRICTIONS
    if (hasActionMatches && actions.length > 0) {
        const actionList = actions.map(a => {
            const parts = a.split('/');
            return parts.length > 3 ? `.../${parts.slice(-2).join('/')}` : a;
        });
        const verb = actions.length === 1 ? 'action' : 'actions';
        details.push(`Applies only when performing ${verb}: ${boldList(actionList)}`);
        summaryParts.push('action-specific restriction');
    }

    // 2. STORAGE CONDITIONS
    const containerNameAttr = findAttr(resourceAttrs, 'containers:name') || findAttr(resourceAttrs, 'containers/name');
    if (containerNameAttr && strings.length > 0) {
        const containerNames = strings.filter(s => !s.includes('/') && !s.includes('*'));
        if (containerNames.length > 0) {
            const verb = hasStringLike ? 'match pattern' : 'be';
            details.push(`Container name must ${verb}: ${boldList(containerNames, ' or ')}`);
            summaryParts.push('restricts container access');
        }
    }

    const blobPathAttr = findAttr(resourceAttrs, 'blobs:path') || findAttr(resourceAttrs, 'blobs/path');
    if (blobPathAttr && strings.length > 0) {
        const pathPatterns = strings.filter(s => s.includes('/') || s.includes('*'));
        if (pathPatterns.length > 0) {
            details.push(`Blob path must match: ${boldList(pathPatterns, ' or ')}`);
            summaryParts.push('restricts blob paths');
        }
    }

    const tagAttr = findAttr(resourceAttrs, 'tags:') || findAttr(requestAttrs, 'tags:');
    if (tagAttr && strings.length > 0) {
        const tagName = friendlyAttributeName(tagAttr.bracket);
        const tagValues = strings.slice(0, 3);
        const verb = hasStringLike ? 'match pattern' : 'equal';
        details.push(`${bold(tagName)} must ${verb}: ${boldList(tagValues, ' or ')}${strings.length > 3 ? '...' : ''}`);
        summaryParts.push('restricts by blob tags');
    }

    if (findAttr(resourceAttrs, 'encryptionscopes:name') && strings.length > 0) {
        details.push(`Encryption scope must be: ${boldList(strings, ' or ')}`);
        summaryParts.push('restricts encryption scope');
    }

    if (findAttr(resourceAttrs, 'ishnsenabled') && booleans.length > 0) {
        const hnsRequired = booleans.includes('true');
        details.push(hnsRequired
            ? 'Storage account must have hierarchical namespace enabled (Data Lake Gen2)'
            : 'Storage account must NOT have hierarchical namespace enabled');
        summaryParts.push('restricts by storage account type');
    }

    // 3. ENVIRONMENT CONDITIONS
    if (findAttr(environmentAttrs, 'isprivatelink') && booleans.includes('true')) {
        details.push('Access must be over a private link connection');
        summaryParts.push('requires private link');
    }

    if (findAttr(environmentAttrs, 'privateendpoints') && strings.length > 0) {
        details.push(`Access restricted to private endpoint(s): ${boldList(strings)}`);
        summaryParts.push('restricts by private endpoint');
    }

    if (findAttr(environmentAttrs, 'subnets') && strings.length > 0) {
        details.push(`Access restricted to subnet(s): ${boldList(strings)}`);
        summaryParts.push('restricts by network');
    }

    if (findAttr(environmentAttrs, 'utcnow')) {
        details.push('Access is restricted based on current date/time');
        summaryParts.push('time-based restriction');
    }

    // 4. PRINCIPAL CONDITIONS
    if (principalAttrs.length > 0) {
        details.push('Evaluates custom security attributes assigned to the requesting principal');
        summaryParts.push('restricts by principal attributes');
    }

    // 5. ROLE ASSIGNMENT CONDITIONS
    const requestHasRoleDefId = hasAttr(requestAttrs, 'roledefinitionid');
    const resourceHasRoleDefId = hasAttr(resourceAttrs, 'roledefinitionid');

    if (resourceAttrs.some(p => p.bracket.toLowerCase() === 'hasobotoken') && booleans.includes('true')) {
        details.push('Requires On-Behalf-Of (OBO) token — request must be made on behalf of a signed-in user');
        summaryParts.push('requires user delegation');
    }

    if (guids.length > 0 && (requestHasRoleDefId || resourceHasRoleDefId)) {
        const guidDisplay = codeList(guids);

        if (requestHasRoleDefId) {
            const verb = hasGuidNotEquals ? 'Cannot assign' : 'Can only assign';
            details.push(`${verb} role definitions: ${guidDisplay}`);
            summaryParts.push(hasGuidNotEquals ? 'restricts role assignment creation' : 'restricts assignable roles');
        }

        if (resourceHasRoleDefId) {
            const verb = hasGuidNotEquals ? 'Cannot delete' : 'Can only delete';
            details.push(`${verb} role assignments where role definition ID is: ${guidDisplay}`);
            summaryParts.push(hasGuidNotEquals ? 'restricts role assignment deletion' : 'restricts removable roles');
        }

        if (requestHasRoleDefId && resourceHasRoleDefId) {
            summaryParts.length = 0;
            summaryParts.push('constrained role delegation');
        }
    }

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

    // SUMMARY
    let summary = 'ABAC condition';
    if (summaryParts.length > 0) {
        const uniqueParts = [...new Set(summaryParts)];
        summary = uniqueParts.map((p, i) => i === 0 ? p.charAt(0).toUpperCase() + p.slice(1) : p).join(', ');
    }

    // FALLBACK
    if (details.length === 0) {
        details.push('This condition uses Azure ABAC to provide fine-grained access control');

        if (requestAttrs.length > 0) details.push('Evaluates attributes of the incoming request');
        if (resourceAttrs.length > 0) details.push('Evaluates attributes of the target resource');
        if (principalAttrs.length > 0) details.push('Evaluates custom security attributes of the requesting principal');
        if (environmentAttrs.length > 0) details.push('Evaluates environment conditions (network, time)');
        if (hasActionMatches) details.push('Applies only to specific actions');
        if (hasSubOperationMatches) details.push('Distinguishes between sub-operations');
        if (guids.length > 0) details.push(`References ${guids.length} GUID value(s)`);
        if (strings.length > 0 && strings.length <= 5) {
            details.push(`Compares against values: ${strings.join(', ')}`);
        } else if (strings.length > 5) {
            details.push(`Compares against ${strings.length} string values`);
        }
    }

    return { summary, details };
}

// =============================================================================
// Alpine.js Component
// =============================================================================

/** @param {string} rawCondition */
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

        toggleExplanation() {
            this.showExplanation = !this.showExplanation;
            if (this.showExplanation && !this.explanation) {
                this.explanation = explainCondition(rawCondition);
            }
        },

        getExplanation() {
            this.explanation ??= explainCondition(rawCondition);
            return this.explanation;
        },

        /** @param {string} condition */
        parseCondition(condition) {
            const tokens = this.tokenize(condition);
            let indent = 0;

            /** @type {FormattedLine[]} */
            const lines = tokens.map(token => {
                switch (token.type) {
                    case 'open':
                        return { indent: indent++, html: this.highlight('(') };
                    case 'close':
                        indent = Math.max(0, indent - 1);
                        return { indent, html: this.highlight(')') };
                    default:
                        return { indent, html: this.highlight(token.value) };
                }
            });

            return lines.length > 0 ? lines : [{ indent: 0, html: this.highlight(condition) }];
        },

        /** @param {string} condition @returns {StructureToken[]} */
        tokenize(condition) {
            return tokenizeStructure(condition);
        },

        /** @param {string} str */
        escapeHtml(str) {
            return escapeHtml(str);
        },

        /** @param {string} text */
        highlight(text) {
            return highlight(text);
        }
    };
}

// =============================================================================
// Exports
// =============================================================================

// @ts-ignore - CommonJS export for Node.js test environment
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { conditionFormatter, explainCondition, normalizeGuid, friendlyAttributeName, scanTokens, matchesWordAt, ESCAPE_MAP };
}
