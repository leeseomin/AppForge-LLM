import assert from 'node:assert/strict';
import test from 'node:test';

import { DEFAULT_LOCALE, normalizeLocale, translate } from '../src/i18n.ts';

test('defaults to Korean when no supported locale is stored', () => {
  assert.equal(DEFAULT_LOCALE, 'ko');
  assert.equal(normalizeLocale(null), 'ko');
  assert.equal(normalizeLocale('unsupported'), 'ko');
  assert.equal(translate(normalizeLocale(null), 'hero.title'), '프롬프트 하나로 자율적으로 앱 생성.');
});

test('returns the English screen copy after selecting English', () => {
  assert.equal(normalizeLocale('en'), 'en');
  assert.equal(translate('en', 'hero.title'), 'Build apps autonomously from a single prompt.');
  assert.equal(translate('en', 'common.stepOf', { current: 2, total: 7 }), '2 of 7');
});

test('labels the separate app reload control in both supported languages', () => {
  assert.equal(translate('ko', 'topbar.reloadApp'), '앱 다시 불러오기');
  assert.equal(
    translate('ko', 'topbar.reloadConfirm'),
    '화면 상태와 입력 중인 내용이 사라질 수 있습니다. 앱을 다시 불러올까요?',
  );
  assert.equal(translate('en', 'topbar.reloadApp'), 'Reload app');
  assert.equal(
    translate('en', 'topbar.reloadConfirm'),
    'Screen state and text in progress may be lost. Reload the app?',
  );
});
