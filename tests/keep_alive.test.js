const assert = require('node:assert/strict');
const test = require('node:test');
const { validatedTarget } = require('../keep_alive');

test('accepts only a public Streamlit HTTPS app URL', () => {
  assert.equal(validatedTarget('https://cbt-exam-system.streamlit.app/'), 'https://cbt-exam-system.streamlit.app/');
  for (const value of [
    'http://cbt-exam-system.streamlit.app/',
    'https://cbt-exam-system.streamlit.app.evil.example/',
    'https://127.0.0.1/',
    'https://cbt-exam-system.streamlit.app/?token=secret',
    'https://user:password@cbt-exam-system.streamlit.app/',
  ]) {
    assert.throws(() => validatedTarget(value));
  }
});
