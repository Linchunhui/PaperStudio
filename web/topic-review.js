document.querySelectorAll('[data-topic-action]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const { topicAction, version, change } = form.dataset;
    const body = { version: Number(version) };
    if (change) {
      body.change_id = change;
      body.decision = event.submitter?.value || 'approved';
      body.note = form.elements.note.value;
    }
    const feedback = document.querySelector('#topic-feedback');
    form.append(feedback);
    feedback.textContent = '';
    const buttons = form.querySelectorAll('button');
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(`/api/admin/topics/${encodeURIComponent(document.body.dataset.topic)}/${topicAction}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': document.querySelector('[name="csrf-token"]').content,
        },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const { detail } = await response.json();
        throw new Error(typeof detail === 'string' ? detail : '请检查审核内容后重试');
      }
      location.reload();
    } catch (error) {
      feedback.textContent = error.message;
      buttons.forEach(button => { button.disabled = false; });
    }
  });
});
