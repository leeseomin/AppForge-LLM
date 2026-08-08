<script setup lang="ts">
import { ref, watch } from 'vue';
import DOMPurify from 'dompurify';
import { Marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import markedKatex from 'marked-katex-extension';
import { createHighlighterCore } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';
import githubDark from '@shikijs/themes/github-dark';
import 'katex/dist/katex.min.css';

const props = defineProps<{
  content: string;
}>();

const html = ref('');
const rendering = ref(false);
let renderVersion = 0;
let mermaidSequence = 0;

const highlighterPromise = createHighlighterCore({
  themes: [githubDark],
  langs: [],
  engine: createJavaScriptRegexEngine(),
});

const languageAliases: Record<string, string> = {
  cs: 'csharp', js: 'javascript', md: 'markdown', py: 'python',
  sh: 'bash', shell: 'bash', ts: 'typescript', yml: 'yaml',
};
const languageLoaders: Record<string, () => Promise<{ default: unknown }>> = {
  bash: () => import('@shikijs/langs/bash'),
  cpp: () => import('@shikijs/langs/cpp'),
  csharp: () => import('@shikijs/langs/csharp'),
  css: () => import('@shikijs/langs/css'),
  go: () => import('@shikijs/langs/go'),
  html: () => import('@shikijs/langs/html'),
  java: () => import('@shikijs/langs/java'),
  javascript: () => import('@shikijs/langs/javascript'),
  json: () => import('@shikijs/langs/json'),
  jsx: () => import('@shikijs/langs/jsx'),
  markdown: () => import('@shikijs/langs/markdown'),
  python: () => import('@shikijs/langs/python'),
  rust: () => import('@shikijs/langs/rust'),
  sql: () => import('@shikijs/langs/sql'),
  tsx: () => import('@shikijs/langs/tsx'),
  typescript: () => import('@shikijs/langs/typescript'),
  vue: () => import('@shikijs/langs/vue'),
  yaml: () => import('@shikijs/langs/yaml'),
};
const loadedLanguages = new Set<string>();
let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null;

async function getMermaid(): Promise<typeof import('mermaid')['default']> {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((module) => {
      const instance = module.default;
      instance.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'dark',
        htmlLabels: false,
        suppressErrorRendering: true,
      });
      return instance;
    });
  }
  return mermaidPromise;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character] || character);
}

async function highlightCode(code: string, language: string): Promise<string> {
  if (language === 'mermaid') return escapeHtml(code);
  try {
    const highlighter = await highlighterPromise;
    const normalizedLanguage = languageAliases[language] || language;
    const loader = languageLoaders[normalizedLanguage];
    if (loader && !loadedLanguages.has(normalizedLanguage)) {
      const registration = (await loader()).default;
      await highlighter.loadLanguage(registration as Parameters<typeof highlighter.loadLanguage>[0]);
      loadedLanguages.add(normalizedLanguage);
    }
    const result = highlighter.codeToTokens(code, {
      lang: loader ? normalizedLanguage : 'text',
      theme: 'github-dark',
    });
    return result.tokens
      .map((line) => line.map((token) => {
        const styles: string[] = [];
        if (token.color) styles.push(`color:${token.color}`);
        if ((token.fontStyle || 0) & 1) styles.push('font-style:italic');
        if ((token.fontStyle || 0) & 2) styles.push('font-weight:700');
        if ((token.fontStyle || 0) & 4) styles.push('text-decoration:underline');
        const style = styles.length ? ` style="${styles.join(';')}"` : '';
        return `<span${style}>${escapeHtml(token.content)}</span>`;
      }).join(''))
      .join('\n');
  } catch {
    return escapeHtml(code);
  }
}

const parser = new Marked(
  markedKatex({ throwOnError: false, nonStandard: true }),
  markedHighlight({
    async: true,
    langPrefix: 'language-',
    emptyLangClass: 'language-text',
    highlight: highlightCode,
  }),
);
parser.setOptions({ gfm: true, breaks: true });

const sanitizeOptions = {
  USE_PROFILES: { html: true, svg: true, svgFilters: true },
  ADD_ATTR: ['target'],
};

async function renderMarkdown(source: string): Promise<string> {
  const parsed = await parser.parse(source || '');
  const template = document.createElement('template');
  template.innerHTML = String(DOMPurify.sanitize(String(parsed), sanitizeOptions));

  for (const anchor of template.content.querySelectorAll<HTMLAnchorElement>('a')) {
    anchor.target = '_blank';
    anchor.rel = 'noreferrer noopener';
  }

  const mermaidBlocks = [...template.content.querySelectorAll<HTMLElement>('pre > code.language-mermaid')];
  const mermaid = mermaidBlocks.length ? await getMermaid() : null;
  for (const block of mermaidBlocks) {
    const sourceText = block.textContent || '';
    const container = document.createElement('div');
    container.className = 'mermaid-diagram';
    try {
      const result = await mermaid!.render(`appforge-mermaid-${++mermaidSequence}`, sourceText);
      container.innerHTML = String(DOMPurify.sanitize(result.svg, sanitizeOptions));
      block.parentElement?.replaceWith(container);
    } catch {
      block.parentElement?.classList.add('mermaid-error');
      block.parentElement?.setAttribute('title', 'Mermaid 다이어그램을 렌더링하지 못했습니다.');
    }
  }

  return String(DOMPurify.sanitize(template.innerHTML, sanitizeOptions));
}

watch(
  () => props.content,
  async (content) => {
    const version = ++renderVersion;
    rendering.value = true;
    try {
      const rendered = await renderMarkdown(content);
      if (version === renderVersion) html.value = rendered;
    } finally {
      if (version === renderVersion) rendering.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="markdown-content" :aria-busy="rendering ? 'true' : 'false'">
    <p v-if="rendering && !html" class="markdown-loading">출력을 렌더링하는 중…</p>
    <!-- Content is sanitized before this assignment. -->
    <div v-else v-html="html"></div>
  </div>
</template>
