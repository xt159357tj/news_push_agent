const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function makeNode(tag = 'div') {
  return {
    tag, children: [], value: '', textContent: '', className: '', href: '',
    setAttribute() {}, appendChild(c) { this.children.push(c); return c; },
  };
}

function loadApp() {
  const elements = Object.fromEntries([
    'delivery_time', 'delivery_hour', 'delivery_minute', 'delivery_time_display',
    'delivery_hour_value', 'delivery_minute_value', 'delivery_timezone',
  ].map(id => [id, makeNode()]));
  const context = vm.createContext({
    Intl, Date, console,
    document: {
      getElementById: id => elements[id],
      createElement: makeNode,
      createTextNode: text => ({ text }),
    },
  });
  const source = readFileSync('app/static/app.js', 'utf8').replace(/init\(\);\s*$/, '');
  vm.runInContext(source, context);
  return { context, elements };
}

test('history converts UTC to profile timezone regardless of browser timezone', () => {
  const { context } = loadApp();
  assert.match(context.formatDigestTime('2026-10-20T06:30:25+00:00', 'Asia/Shanghai'), /14:30:25/);
  assert.match(context.formatDigestTime('2026-10-20T06:30:25', 'Asia/Shanghai'), /14:30:25/);
  assert.match(context.formatDigestTime('2026-10-20T23:30:25Z', 'Asia/Shanghai'), /2026\/10\/21.*07:30:25/);
  assert.match(context.formatDigestTime('2026-10-20T06:30:25Z', 'UTC'), /06:30:25/);
});

test('time sliders restore saved minutes and serialize midnight and day-end', () => {
  const { context, elements } = loadApp();
  context.setDeliveryTime('08:37');
  assert.equal(elements.delivery_hour.value, '8');
  assert.equal(elements.delivery_minute.value, '37');
  assert.equal(elements.delivery_time_display.textContent, '08:37');
  for (const [hour, minute, expected] of [['0', '0', '00:00'], ['23', '59', '23:59']]) {
    elements.delivery_hour.value = hour;
    elements.delivery_minute.value = minute;
    context.updateDeliveryTime();
    assert.equal(elements.delivery_time.value, expected);
    assert.equal(elements.delivery_time_display.textContent, expected);
  }
});

test('changing profile timezone updates the delivery timezone hint', () => {
  const { context, elements } = loadApp();
  context.updateTimezone('UTC');
  assert.equal(elements.delivery_timezone.textContent, 'UTC');
  context.updateTimezone('Not/AZone');
  assert.equal(elements.delivery_timezone.textContent, 'Asia/Shanghai');
});

test('chat text renders links as anchors and never as markup', () => {
  const { context } = loadApp();
  const box = makeNode();
  context.renderRichText(box, '看 [原文](https://a.com/x) 或 https://b.com/y，<img src=x onerror=alert(1)>');
  const anchors = box.children.filter(c => c.tag === 'a');
  assert.deepEqual(anchors.map(a => a.href), ['https://a.com/x', 'https://b.com/y']);
  assert.deepEqual(anchors.map(a => a.textContent), ['原文', 'https://b.com/y']);
  assert.ok(anchors.every(a => a.rel === 'noopener noreferrer'));
  const plain = box.children.filter(c => c.text !== undefined).map(c => c.text).join('');
  assert.ok(plain.includes('<img src=x onerror=alert(1)>'));
  assert.ok(box.children.every(c => c.tag !== 'img'));
});

test('javascript: urls in chat text are left as plain text', () => {
  const { context } = loadApp();
  const box = makeNode();
  context.renderRichText(box, '[点我](javascript:alert(1))');
  assert.equal(box.children.filter(c => c.tag === 'a').length, 0);
});
