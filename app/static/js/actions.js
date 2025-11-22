// app/static/js/actions.js
// Usage: await callProtectedAction('issue_warning', { booking_id });

async function callProtectedAction(action, payload, { timeoutMs = 8000 } = {}) {
  // simple client-side sanity check (server must still enforce)
  if (!action || typeof action !== 'string') throw new Error('invalid action');
  if (!payload || typeof payload !== 'object') throw new Error('invalid payload');

  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);

  try {
    // 1) ask server to prepare (server will verify ownership)
    const prepareRes = await fetch('/action/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'same-origin',
      signal: controller.signal,
      body: JSON.stringify({ action, ...payload }),
    });

    // gather body text for helpful errors (don't assume JSON on error)
    const prepareText = await prepareRes.text();
    if (!prepareRes.ok) {
      throw new Error(`prepare failed (${prepareRes.status}): ${prepareText}`);
    }

    // try to parse JSON; if parsing fails, include raw text for debugging
    let prepared;
    try {
      prepared = JSON.parse(prepareText);
    } catch (err) {
      throw new Error('prepare response was not valid JSON: ' + prepareText);
    }

    // support either naming (action_token or actionToken)
    const actionToken = prepared.action_token ?? prepared.actionToken;
    if (!actionToken) {
      throw new Error('no action token returned from prepare');
    }

    // optional: check server-provided expiry (informational)
    if (prepared.expires_in && typeof prepared.expires_in === 'number') {
      // if needed, you can warn/refresh before expiry; here we just log
      console.debug('action token expires_in (s):', prepared.expires_in);
    }

    // 2) call the protected action with the short-lived action token
    const res = await fetch('/action/' + encodeURIComponent(action), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'x-action-token': actionToken,
      },
      credentials: 'same-origin',
      signal: controller.signal,
      body: JSON.stringify(payload),
    });

    const resText = await res.text();
    if (!res.ok) {
      throw new Error(`action failed (${res.status}): ${resText}`);
    }

    try {
      return JSON.parse(resText);
    } catch (err) {
      // if server returned empty or non-json success, return raw text
      return { raw: resText };
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('network timeout');
    }
    // rethrow to caller with helpful message
    throw err;
  } finally {
    clearTimeout(id);
  }
}
