import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

it('keeps shared preview validation and the public daily entry free of owner-client dependencies', () => {
  const visited = new Set<string>();
  function visit(file: string) {
    const absolute = resolve(file);
    if (visited.has(absolute)) return;
    visited.add(absolute);
    const source = ts.createSourceFile(
      absolute, readFileSync(absolute, 'utf8'), ts.ScriptTarget.Latest, true,
    );
    function dependency(specifier: string) {
      if (!specifier.startsWith('.')) return;
      const base = resolve(dirname(absolute), specifier);
      const target = [base, `${base}.ts`, `${base}.tsx`, resolve(base, 'index.ts'), resolve(base, 'index.tsx')]
        .find(candidate => /\.[cm]?tsx?$/.test(candidate) && existsSync(candidate));
      if (target) visit(target);
    }
    function walk(node: ts.Node) {
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        const clause = node.importClause;
        const named = clause?.namedBindings;
        const typeOnly = clause?.isTypeOnly || (named && ts.isNamedImports(named)
          && !clause?.name && named.elements.every(element => element.isTypeOnly));
        if (!typeOnly) dependency(node.moduleSpecifier.text);
      }
      if (ts.isExportDeclaration(node) && !node.isTypeOnly
        && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        dependency(node.moduleSpecifier.text);
      }
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
        && node.arguments[0] && ts.isStringLiteral(node.arguments[0])) {
        dependency(node.arguments[0].text);
      }
      ts.forEachChild(node, walk);
    }
    walk(source);
  }
  visit('src/features/news/api.ts');
  visit('src/features/news/PromotedSummaryPreview.tsx');
  visit('src/features/public-daily-ai/PublicDailyAIEntry.tsx');
  const modules = [...visited].map(file => file.replaceAll('\\', '/'));
  expect(modules.some(file => file.endsWith('/PublicDailyAI.tsx'))).toBe(true);
  expect(modules.some(file => file.endsWith('/PublicCitationSource.tsx'))).toBe(true);
  for (const file of modules) {
    expect(file).not.toMatch(/\/features\/(?:ai|owner)\//);
    expect(file).not.toContain('publication-client');
  }
});
