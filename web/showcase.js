const demoOverlay = document.getElementById('showcaseDemoOverlay');
const demoFrame = document.getElementById('showcaseDemoFrame');
const demoTitle = document.getElementById('showcaseDemoTitle');
const backToPresentation = document.getElementById('backToPresentation');

function openShowcaseDemo(url, title) {
  if (!demoOverlay || !demoFrame) return;
  demoFrame.src = url;
  if (demoTitle) demoTitle.textContent = title || 'Live Demo';
  demoOverlay.classList.add('open');
  demoOverlay.setAttribute('aria-hidden', 'false');
  document.body.classList.add('showcase-demo-open');
  backToPresentation?.focus();
}

function closeShowcaseDemo() {
  if (!demoOverlay) return;
  demoOverlay.classList.remove('open');
  demoOverlay.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('showcase-demo-open');
  document.querySelector('.showcase-demo-target')?.focus();
}

document.querySelectorAll('.showcase-demo-target').forEach(link => {
  link.addEventListener('click', event => {
    event.preventDefault();
    openShowcaseDemo(link.dataset.demoUrl || link.href, link.dataset.demoTitle);
  });
});

backToPresentation?.addEventListener('click', closeShowcaseDemo);

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && demoOverlay?.classList.contains('open')) {
    closeShowcaseDemo();
  }
});
