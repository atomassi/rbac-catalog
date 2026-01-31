/**
 * Unit tests for condition-formatter.js
 * Tests ABAC condition parsing, tokenization, and syntax highlighting
 */
import { describe, it, expect, beforeEach } from 'vitest';
const { conditionFormatter, ESCAPE_MAP } = require('../../azurerbac/web/static/js/condition-formatter.js');

describe('conditionFormatter', () => {
    /** @type {ReturnType<typeof conditionFormatter>} */
    let formatter;

    beforeEach(() => {
        // Create a fresh formatter instance for each test
        formatter = conditionFormatter('');
    });

    describe('escapeHtml', () => {
        it('should escape ampersand', () => {
            expect(formatter.escapeHtml('A & B')).toBe('A &amp; B');
        });

        it('should escape less-than', () => {
            expect(formatter.escapeHtml('A < B')).toBe('A &lt; B');
        });

        it('should escape greater-than', () => {
            expect(formatter.escapeHtml('A > B')).toBe('A &gt; B');
        });

        it('should escape double quotes', () => {
            expect(formatter.escapeHtml('A "B" C')).toBe('A &quot;B&quot; C');
        });

        it('should escape single quotes', () => {
            expect(formatter.escapeHtml("A 'B' C")).toBe('A &#39;B&#39; C');
        });

        it('should escape all special characters in one pass', () => {
            expect(formatter.escapeHtml('<script>"alert(\'xss\')"</script>'))
                .toBe('&lt;script&gt;&quot;alert(&#39;xss&#39;)&quot;&lt;/script&gt;');
        });

        it('should handle empty string', () => {
            expect(formatter.escapeHtml('')).toBe('');
        });

        it('should not modify strings without special characters', () => {
            expect(formatter.escapeHtml('ActionMatches')).toBe('ActionMatches');
        });
    });

    describe('ESCAPE_MAP', () => {
        it('should have all required escape mappings', () => {
            expect(ESCAPE_MAP).toEqual({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            });
        });
    });

    describe('tokenize', () => {
        it('should tokenize simple expression without parens', () => {
            const tokens = formatter.tokenize('@Request[isOwner] boolequals true');
            expect(tokens).toEqual([
                { type: 'expr', value: '@Request[isOwner] boolequals true' }
            ]);
        });

        it('should tokenize parentheses as open/close tokens', () => {
            const tokens = formatter.tokenize('(A)');
            expect(tokens).toEqual([
                { type: 'open', value: '(' },
                { type: 'expr', value: 'A' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should tokenize AND operator', () => {
            const tokens = formatter.tokenize('(A AND B)');
            expect(tokens).toEqual([
                { type: 'open', value: '(' },
                { type: 'expr', value: 'A' },
                { type: 'operator', value: 'AND' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should tokenize OR operator', () => {
            const tokens = formatter.tokenize('(A OR B)');
            expect(tokens).toEqual([
                { type: 'open', value: '(' },
                { type: 'expr', value: 'A' },
                { type: 'operator', value: 'OR' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should preserve && operator', () => {
            const tokens = formatter.tokenize('(A && B)');
            expect(tokens).toEqual([
                { type: 'open', value: '(' },
                { type: 'expr', value: 'A' },
                { type: 'operator', value: '&&' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should preserve || operator', () => {
            const tokens = formatter.tokenize('(A || B)');
            expect(tokens).toEqual([
                { type: 'open', value: '(' },
                { type: 'expr', value: 'A' },
                { type: 'operator', value: '||' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should handle AND( without space (lookahead regex)', () => {
            const tokens = formatter.tokenize('A AND(B)');
            expect(tokens).toEqual([
                { type: 'expr', value: 'A' },
                { type: 'operator', value: 'AND' },
                { type: 'open', value: '(' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should handle OR( without space', () => {
            const tokens = formatter.tokenize('A OR(B)');
            expect(tokens).toEqual([
                { type: 'expr', value: 'A' },
                { type: 'operator', value: 'OR' },
                { type: 'open', value: '(' },
                { type: 'expr', value: 'B' },
                { type: 'close', value: ')' }
            ]);
        });

        it('should not split inside brackets []', () => {
            const tokens = formatter.tokenize('@Request[Microsoft.Storage/containers:name]');
            expect(tokens).toEqual([
                { type: 'expr', value: '@Request[Microsoft.Storage/containers:name]' }
            ]);
        });

        it('should not split inside braces {}', () => {
            const tokens = formatter.tokenize('ForAnyOfAnyValues:GuidEquals{abc-123, def-456}');
            expect(tokens).toEqual([
                { type: 'expr', value: 'ForAnyOfAnyValues:GuidEquals{abc-123, def-456}' }
            ]);
        });

        it('should keep !(...) negation as single expression', () => {
            const tokens = formatter.tokenize("!(ActionMatches{'Microsoft.Storage/read'})");
            expect(tokens).toEqual([
                { type: 'expr', value: "!(ActionMatches{'Microsoft.Storage/read'})" }
            ]);
        });

        it('should handle ! (...) with space before paren', () => {
            // Known limitation: space between ! and ( causes the negation to not be grouped
            // This is documented in the tokenizer comments
            const tokens = formatter.tokenize("! (ActionMatches{'test'})");
            // With space, it splits into separate tokens (known behavior)
            expect(tokens.length).toBeGreaterThan(1);
        });

        it('should handle nested negation - nightmare case', () => {
            const condition = "!(ActionMatches{'a'} AND !(ActionMatches{'b'}))";
            const tokens = formatter.tokenize(condition);
            // Due to the complexity, this should at least not crash
            // and preserve the structure reasonably
            expect(tokens.length).toBeGreaterThan(0);
            // The outer !(...) should be kept together at minimum
        });

        it('should handle complex real-world condition', () => {
            const condition = "(!(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'}) || @Request[Microsoft.Storage/storageAccounts/blobServices/containers:name] stringequalsignorecase 'test' && @Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags:Project<$key_case_sensitive$>] forallofanyvalues:stringlikeignorecase {'*'})";
            const tokens = formatter.tokenize(condition);
            
            // Should have parsed into multiple tokens
            expect(tokens.length).toBeGreaterThan(1);
            
            // Should have the operators
            const operators = tokens.filter(t => t.type === 'operator');
            expect(operators.some(o => o.value === '||')).toBe(true);
            expect(operators.some(o => o.value === '&&')).toBe(true);
        });

        it('should handle case-insensitive operators', () => {
            const tokens = formatter.tokenize('(A and B or C)');
            const operators = tokens.filter(t => t.type === 'operator');
            expect(operators.map(o => o.value)).toEqual(['and', 'or']);
        });
    });

    describe('parseCondition', () => {
        it('should return formatted lines with indentation', () => {
            const fmt = conditionFormatter('(A AND B)');
            fmt.init();
            
            expect(fmt.lines.length).toBe(5);
            expect(fmt.lines[0].indent).toBe(0); // (
            expect(fmt.lines[1].indent).toBe(1); // A
            expect(fmt.lines[2].indent).toBe(1); // AND
            expect(fmt.lines[3].indent).toBe(1); // B
            expect(fmt.lines[4].indent).toBe(0); // )
        });

        it('should handle nested parentheses with proper indentation', () => {
            const fmt = conditionFormatter('((A))');
            fmt.init();
            
            expect(fmt.lines.length).toBe(5);
            expect(fmt.lines[0].indent).toBe(0); // outer (
            expect(fmt.lines[1].indent).toBe(1); // inner (
            expect(fmt.lines[2].indent).toBe(2); // A
            expect(fmt.lines[3].indent).toBe(1); // inner )
            expect(fmt.lines[4].indent).toBe(0); // outer )
        });

        it('should return single line for simple condition', () => {
            const fmt = conditionFormatter('@Request[isOwner] boolequals true');
            fmt.init();
            
            expect(fmt.lines.length).toBe(1);
            expect(fmt.lines[0].indent).toBe(0);
        });

        it('should fallback to single line if tokenizer returns empty', () => {
            const fmt = conditionFormatter('');
            fmt.init();
            
            expect(fmt.lines.length).toBe(1);
        });
    });

    describe('highlight', () => {
        it('should highlight AND operator as blue bold', () => {
            const result = formatter.highlight('AND');
            expect(result).toContain('var(--cond-blue)');
            expect(result).toContain('font-weight: 600');
        });

        it('should highlight OR operator as blue bold', () => {
            const result = formatter.highlight('OR');
            expect(result).toContain('var(--cond-blue)');
            expect(result).toContain('font-weight: 600');
        });

        it('should highlight && operator as blue bold', () => {
            const result = formatter.highlight('&&');
            expect(result).toContain('var(--cond-blue)');
            expect(result).toContain('font-weight: 600');
            // The && should be escaped in output
            expect(result).toContain('&amp;&amp;');
        });

        it('should highlight || operator as blue bold', () => {
            const result = formatter.highlight('||');
            expect(result).toContain('var(--cond-blue)');
            expect(result).toContain('font-weight: 600');
        });

        it('should highlight parentheses as gray', () => {
            const open = formatter.highlight('(');
            const close = formatter.highlight(')');
            expect(open).toContain('var(--cond-gray)');
            expect(close).toContain('var(--cond-gray)');
        });

        it('should highlight ActionMatches as blue', () => {
            const result = formatter.highlight("ActionMatches{'test'}");
            expect(result).toContain('<span style="color: var(--cond-blue)">ActionMatches</span>');
        });

        it('should highlight !ActionMatches with red ! and blue ActionMatches', () => {
            const result = formatter.highlight("!ActionMatches{'test'}");
            expect(result).toContain('var(--cond-red)');
            expect(result).toContain('var(--cond-blue)');
        });

        it('should highlight ForAnyOfAnyValues:GuidEquals as blue', () => {
            const result = formatter.highlight('ForAnyOfAnyValues:GuidEquals{abc-123}');
            expect(result).toContain('<span style="color: var(--cond-blue)">ForAnyOfAnyValues:GuidEquals</span>');
        });

        it('should highlight ForAnyOfAllValues:GuidNotEquals as blue', () => {
            const result = formatter.highlight('ForAnyOfAllValues:GuidNotEquals{abc}');
            expect(result).toContain('var(--cond-blue)');
        });

        it('should highlight boolequals as blue', () => {
            const result = formatter.highlight('@Request[isOwner] boolequals true');
            expect(result).toContain('<span style="color: var(--cond-blue)">boolequals</span>');
        });

        it('should highlight stringequalsignorecase as blue', () => {
            const result = formatter.highlight('@Request[name] stringequalsignorecase test');
            expect(result).toContain('var(--cond-blue)');
        });

        it('should highlight true/false as green', () => {
            const resultTrue = formatter.highlight('boolequals true');
            const resultFalse = formatter.highlight('boolequals false');
            expect(resultTrue).toContain('<span style="color: var(--cond-green)">true</span>');
            expect(resultFalse).toContain('<span style="color: var(--cond-green)">false</span>');
        });

        it('should highlight @Request as green', () => {
            const result = formatter.highlight('@Request[isOwner]');
            expect(result).toContain('<span style="color: var(--cond-green)">@Request</span>');
        });

        it('should highlight @Resource as green', () => {
            const result = formatter.highlight('@Resource[name]');
            expect(result).toContain('<span style="color: var(--cond-green)">@Resource</span>');
        });

        it('should highlight strings in single quotes as red (escaped)', () => {
            // Quotes are escaped first, then matched
            const result = formatter.highlight("'Microsoft.Storage/read'");
            expect(result).toContain('var(--cond-red)');
            expect(result).toContain('Microsoft.Storage/read');
        });

        it('should highlight GUIDs in braces as green', () => {
            const result = formatter.highlight('{abc-123-def}');
            expect(result).toContain('<span style="color: var(--cond-green)">abc-123-def</span>');
        });

        it('should highlight multiple GUIDs in braces', () => {
            const result = formatter.highlight('{abc-123, def-456}');
            expect(result).toContain('var(--cond-green)');
        });

        it('should escape HTML before highlighting (XSS prevention)', () => {
            const result = formatter.highlight('<script>alert("xss")</script>');
            expect(result).toContain('&lt;script&gt;');
            expect(result).not.toContain('<script>');
        });
    });

    describe('full integration', () => {
        it('should format and highlight a complete real condition', () => {
            const condition = "(@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] ForAnyOfAnyValues:GuidEquals{91e8ffa9-6ff7-4a44-a192-5f40f16d0458, a65fcd79-32a6-4aaf-814c-f7d04b71a355})";
            const fmt = conditionFormatter(condition);
            fmt.init();
            
            // Should have parsed into lines
            expect(fmt.lines.length).toBeGreaterThan(1);
            
            // First line should be opening paren
            expect(fmt.lines[0].html).toContain('(');
            
            // Should contain highlighted elements
            const allHtml = fmt.lines.map(l => l.html).join('');
            expect(allHtml).toContain('var(--cond-green)'); // @Resource and GUIDs
            expect(allHtml).toContain('var(--cond-blue)');  // ForAnyOfAnyValues:GuidEquals
        });

        it('should handle condition with multiple operators', () => {
            const condition = "(A AND B OR C)";
            const fmt = conditionFormatter(condition);
            fmt.init();
            
            const operators = fmt.lines.filter(l => 
                l.html.includes('AND') || l.html.includes('OR')
            );
            expect(operators.length).toBe(2);
        });

        it('should handle empty condition gracefully', () => {
            const fmt = conditionFormatter('');
            fmt.init();
            
            // Should not crash and return at least one line
            expect(fmt.lines.length).toBeGreaterThanOrEqual(1);
        });

        it('should handle whitespace-only condition', () => {
            const fmt = conditionFormatter('   ');
            fmt.init();
            
            // Should not crash
            expect(fmt.lines).toBeDefined();
        });
    });
});

// Import explainCondition for testing
const { explainCondition, normalizeGuid, friendlyAttributeName } = require('../../azurerbac/web/static/js/condition-formatter.js');

describe('explainCondition', () => {
    describe('role assignment conditions', () => {
        it('should explain @Request RoleDefinitionId with GuidEquals as role assignment creation restriction', () => {
            const condition = `(
                !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
                OR
                @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}
            )`;
            const result = explainCondition(condition);
            expect(result.summary.toLowerCase()).toContain('role');
            expect(result.details.some(d => d.toLowerCase().includes('assign'))).toBe(true);
        });

        it('should explain @Resource RoleDefinitionId with GuidEquals as role assignment deletion restriction', () => {
            const condition = `(
                !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
                OR
                @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}
            )`;
            const result = explainCondition(condition);
            expect(result.summary.toLowerCase()).toContain('role');
            expect(result.details.some(d => d.toLowerCase().includes('delete') || d.toLowerCase().includes('remove'))).toBe(true);
        });

        it('should explain constrained delegation (both Request and Resource RoleDefinitionId)', () => {
            const condition = `(
                @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}
                AND
                @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}
            )`;
            const result = explainCondition(condition);
            expect(result.details.length).toBeGreaterThan(0);
        });

        it('should explain principal type restriction', () => {
            const condition = `@Request[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase 'ServicePrincipal'`;
            const result = explainCondition(condition);
            expect(result.details.some(d => d.toLowerCase().includes('principal'))).toBe(true);
        });

        it('should correctly distinguish GuidNotEquals from GuidEquals', () => {
            const condition = `@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidNotEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}`;
            const result = explainCondition(condition);
            // Should indicate restriction/exclusion, not allowance
            expect(result.details.some(d => d.toLowerCase().includes('cannot') || d.toLowerCase().includes('not'))).toBe(true);
        });
    });

    describe('storage conditions', () => {
        it('should explain container name restriction', () => {
            const condition = `(
                !(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'})
                OR
                @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringEquals 'blobs-example-container'
            )`;
            const result = explainCondition(condition);
            expect(result.summary.toLowerCase()).toContain('container');
            expect(result.details.some(d => d.toLowerCase().includes('container'))).toBe(true);
        });

        it('should explain blob path restriction', () => {
            const condition = `@Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs:path] StringLike 'readonly/*'`;
            const result = explainCondition(condition);
            expect(result.details.some(d => d.toLowerCase().includes('blob') || d.toLowerCase().includes('path'))).toBe(true);
        });

        it('should explain blob tag restriction', () => {
            const condition = `@Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags:Project<$key_case_sensitive$>] StringEquals 'Cascade'`;
            const result = explainCondition(condition);
            expect(result.details.some(d => d.toLowerCase().includes('tag'))).toBe(true);
        });
    });

    describe('environment conditions', () => {
        it('should explain private link requirement', () => {
            const condition = `@Environment[isPrivateLink] BoolEquals true`;
            const result = explainCondition(condition);
            expect(result.summary.toLowerCase()).toContain('private');
            expect(result.details.some(d => d.toLowerCase().includes('private link'))).toBe(true);
        });

        it('should handle UTC now time-based conditions', () => {
            const condition = `@Environment[UtcNow] DateTimeGreaterThan '2024-01-01T00:00:00Z'`;
            const result = explainCondition(condition);
            expect(result.details.some(d => d.toLowerCase().includes('time') || d.toLowerCase().includes('date'))).toBe(true);
        });
    });

    describe('action restrictions', () => {
        it('should explain ActionMatches restrictions', () => {
            const condition = `!(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'})`;
            const result = explainCondition(condition);
            expect(result.details.some(d => d.toLowerCase().includes('action'))).toBe(true);
        });
    });



    describe('fallback behavior', () => {
        it('should provide generic explanation for unknown patterns', () => {
            const condition = `@Custom[SomeAttribute] SomeOperator 'value'`;
            const result = explainCondition(condition);
            expect(result.summary).toBe('ABAC condition');
            expect(result.details.length).toBeGreaterThan(0);
        });

        it('should handle empty condition', () => {
            const result = explainCondition('');
            expect(result.summary).toBeDefined();
            expect(result.details).toBeDefined();
        });
    });
});

describe('normalizeGuid', () => {
    it('should normalize GUID without hyphens', () => {
        expect(normalizeGuid('acdd72a7338548efbd42f606fba81ae7'))
            .toBe('acdd72a7-3385-48ef-bd42-f606fba81ae7');
    });

    it('should keep already-normalized GUIDs unchanged', () => {
        expect(normalizeGuid('acdd72a7-3385-48ef-bd42-f606fba81ae7'))
            .toBe('acdd72a7-3385-48ef-bd42-f606fba81ae7');
    });

    it('should convert to lowercase', () => {
        expect(normalizeGuid('ACDD72A7-3385-48EF-BD42-F606FBA81AE7'))
            .toBe('acdd72a7-3385-48ef-bd42-f606fba81ae7');
    });

    it('should return original for invalid GUIDs', () => {
        expect(normalizeGuid('not-a-guid')).toBe('not-a-guid');
    });

    it('should return original for non-hex characters', () => {
        // 32 characters but not valid hex
        expect(normalizeGuid('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')).toBe('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx');
        expect(normalizeGuid('ghij72a7338548efbd42f606fba81ae7')).toBe('ghij72a7338548efbd42f606fba81ae7');
    });
});

describe('friendlyAttributeName', () => {
    it('should extract tag name from tag attribute path', () => {
        expect(friendlyAttributeName('tags:Project<$key_case_sensitive$>'))
            .toBe('tag "Project"');
    });

    it('should extract resource type and property', () => {
        expect(friendlyAttributeName('Microsoft.Storage/storageAccounts/blobServices/containers:name'))
            .toBe('containers name');
    });

    it('should handle simple property paths', () => {
        expect(friendlyAttributeName('isPrivateLink')).toBe('isPrivateLink');
    });
});
