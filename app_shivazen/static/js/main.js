(function() {
  "use strict";

  /**
   * Apply .scrolled class to the body as the page is scrolled down
   */
  function toggleScrolled() {
    const selectBody = document.querySelector('body');
    const selectHeader = document.querySelector('#header');

    if (!selectHeader) return;

    if (!selectHeader.classList.contains('scroll-up-sticky') && !selectHeader.classList.contains('sticky-top') && !selectHeader.classList.contains('fixed-top')) return;
    window.scrollY > 100 ? selectBody.classList.add('scrolled') : selectBody.classList.remove('scrolled');
  }

  document.addEventListener('scroll', toggleScrolled);
  window.addEventListener('load', toggleScrolled);

  /**
   * Mobile nav toggle
   */
  function setupMobileNav() {
    const mobileNavToggleBtn = document.querySelector('.mobile-nav-toggle');

    if (!mobileNavToggleBtn) return;

    function mobileNavToogle() {
      document.querySelector('body').classList.toggle('mobile-nav-active');
      mobileNavToggleBtn.classList.toggle('bi-list');
      mobileNavToggleBtn.classList.toggle('bi-x');
    }

    mobileNavToggleBtn.addEventListener('click', mobileNavToogle);

    document.querySelectorAll('#navmenu a').forEach(navmenu => {
      navmenu.addEventListener('click', () => {
        if (document.querySelector('.mobile-nav-active')) {
          mobileNavToogle();
        }
      });
    });

    document.querySelectorAll('.navmenu .toggle-dropdown').forEach(navmenu => {
      navmenu.addEventListener('click', function(e) {
        e.preventDefault();
        this.parentNode.classList.toggle('active');
        this.parentNode.nextElementSibling.classList.toggle('dropdown-active');
        e.stopImmediatePropagation();
      });
    });
  }

  /**
   * Scroll top button
   */
  function setupScrollTop() {
    let scrollTop = document.querySelector('.scroll-top');

    if (!scrollTop) return;

    function toggleScrollTop() {
      window.scrollY > 100 ? scrollTop.classList.add('active') : scrollTop.classList.remove('active');
    }

    scrollTop.addEventListener('click', (e) => {
      e.preventDefault();
      window.scrollTo({
        top: 0,
        behavior: 'smooth'
      });
    });

    window.addEventListener('load', toggleScrollTop);
    document.addEventListener('scroll', toggleScrollTop);
  }

  /**
   * Animation on scroll
   */
  function aosInit() {
    if (typeof AOS !== 'undefined') {
      AOS.init({
        duration: 600,
        easing: 'ease-in-out',
        once: true,
        mirror: false
      });
    }
  }

  /**
   * GLightbox
   */
  function setupGlightbox() {
    if (typeof GLightbox !== 'undefined') {
      GLightbox({
        selector: '.glightbox'
      });
    }
  }

  /**
   * Pure Counter
   */
  function setupPureCounter() {
    if (typeof PureCounter !== 'undefined') {
      new PureCounter();
    }
  }

  /**
   * Swiper
   */
  function initSwiper() {
    document.querySelectorAll(".init-swiper").forEach(function(swiperElement) {
      if (!swiperElement) return;

      let config = JSON.parse(
        swiperElement.querySelector(".swiper-config").innerHTML.trim()
      );

      if (swiperElement.classList.contains("swiper-tab")) {
        initSwiperWithCustomPagination(swiperElement, config);
      } else {
        new Swiper(swiperElement, config);
      }
    });
  }

  /**
   * FAQ Toggle
   */
  function setupFaq() {
    document.querySelectorAll('.faq-item h3, .faq-item .faq-toggle, .faq-item .faq-header').forEach((faqItem) => {
      faqItem.addEventListener('click', () => {
        faqItem.parentNode.classList.toggle('faq-active');
      });
    });
  }

  /**
   * Smooth scroll for anchor links
   */
  function setupSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
      anchor.addEventListener('click', function(e) {
        const href = this.getAttribute('href');
        if (href === '#' || href === '#!') return;

        const target = document.querySelector(href);
        if (target) {
          e.preventDefault();
          const headerOffset = 80;
          const elementPosition = target.getBoundingClientRect().top;
          const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

          window.scrollTo({
            top: offsetPosition,
            behavior: 'smooth'
          });
        }
      });
    });
  }

  /**
   * Form validation enhancements
   */
  function setupFormValidation() {
    const forms = document.querySelectorAll('form');

    forms.forEach(form => {
      const inputs = form.querySelectorAll('input, select, textarea');

      inputs.forEach(input => {
        input.addEventListener('blur', function() {
          validateField(this);
        });

        input.addEventListener('input', function() {
          if (this.classList.contains('is-invalid')) {
            validateField(this);
          }
        });
      });

      form.addEventListener('submit', function(e) {
        let isValid = true;

        inputs.forEach(input => {
          if (!validateField(input)) {
            isValid = false;
          }
        });

        if (!isValid) {
          e.preventDefault();
          const firstInvalid = form.querySelector('.is-invalid');
          if (firstInvalid) {
            firstInvalid.focus();
            firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      });
    });
  }

  function validateField(field) {
    const value = field.value.trim();
    let isValid = true;
    let errorMessage = '';

    field.classList.remove('is-valid', 'is-invalid');
    const feedback = field.parentElement.querySelector('.invalid-feedback');
    if (feedback) feedback.remove();

    if (field.hasAttribute('required') && !value) {
      isValid = false;
      errorMessage = 'Este campo é obrigatório';
    }

    if (field.type === 'email' && value) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(value)) {
        isValid = false;
        errorMessage = 'Por favor, insira um e-mail válido';
      }
    }

    if (field.type === 'password' && value) {
      if (value.length < 6) {
        isValid = false;
        errorMessage = 'A senha deve ter pelo menos 6 caracteres';
      }
    }

    if (isValid && value) {
      field.classList.add('is-valid');
    } else if (!isValid) {
      field.classList.add('is-invalid');
      const feedbackDiv = document.createElement('div');
      feedbackDiv.className = 'invalid-feedback';
      feedbackDiv.textContent = errorMessage;
      field.parentElement.appendChild(feedbackDiv);
    }

    return isValid;
  }

  /**
   * Button loading states
   */
  function setupButtonLoading() {
    document.querySelectorAll('form').forEach(form => {
      form.addEventListener('submit', function(e) {
        const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
        if (submitBtn && !submitBtn.disabled) {
          const originalText = submitBtn.innerHTML;
          submitBtn.classList.add('loading');
          submitBtn.disabled = true;

          if (!submitBtn.querySelector('.loading-spinner')) {
            const spinner = document.createElement('span');
            spinner.className = 'loading-spinner';
            submitBtn.innerHTML = '';
            submitBtn.appendChild(spinner);
          }

          setTimeout(() => {
            submitBtn.classList.remove('loading');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
          }, 10000);
        }
      });
    });
  }

  /**
   * CSRF setup for AJAX (Django)
   */
  function setupCSRF() {
    function getCookie(name) {
      let cookieValue = null;
      if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
          const cookie = cookies[i].trim();
          if (cookie.substring(0, name.length + 1) === (name + '=')) {
            cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
            break;
          }
        }
      }
      return cookieValue;
    }

    if (typeof $ !== 'undefined') {
      $.ajaxSetup({
        beforeSend: function(xhr, settings) {
          if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
            xhr.setRequestHeader("X-CSRFToken", getCookie('csrftoken'));
          }
        }
      });
    }
  }

  /**
   * Initialize all components
   */
  function initializeAll() {
    setupMobileNav();
    setupScrollTop();
    setupSmoothScroll();
    aosInit();
    setupGlightbox();
    setupPureCounter();
    initSwiper();
    setupFaq();
    setupFormValidation();
    setupButtonLoading();
    setupCSRF();

    // Periodic scroll check
    setInterval(() => {
      toggleScrolled();
    }, 500);
  }

  /**
   * Floating CTA — show after scrolling past hero
   */
  function setupFloatingCta() {
    const cta = document.getElementById('floatingCta');
    if (!cta) return;
    const hero = document.getElementById('hero');
    const threshold = hero ? hero.offsetHeight : 400;
    window.addEventListener('scroll', function() {
      cta.classList.toggle('visible', window.scrollY > threshold);
    });
  }

  /**
   * Theme toggle (light/dark) with localStorage persistence.
   */
  function setupThemeToggle() {
    var STORAGE_KEY = 'shivazen-theme';
    var root = document.documentElement;
    var body = document.body;
    var btn = document.getElementById('themeToggle');

    function apply(theme) {
      if (theme === 'dark') {
        body.classList.add('theme-dark');
        root.setAttribute('data-theme', 'dark');
      } else {
        body.classList.remove('theme-dark');
        root.setAttribute('data-theme', 'light');
      }
      if (btn) {
        var icon = btn.querySelector('i');
        if (icon) {
          icon.className = theme === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
        }
      }
    }

    var stored = localStorage.getItem(STORAGE_KEY);
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    apply(stored || (prefersDark ? 'dark' : 'light'));

    if (btn) {
      btn.addEventListener('click', function() {
        var next = body.classList.contains('theme-dark') ? 'light' : 'dark';
        localStorage.setItem(STORAGE_KEY, next);
        apply(next);
      });
    }
  }

  /**
   * FAQ accordion (.faq-item > .faq-question + .faq-answer).
   */
  function setupFaqAccordion() {
    document.querySelectorAll('.faq-item .faq-question').forEach(function(q) {
      q.addEventListener('click', function() {
        var item = q.closest('.faq-item');
        if (!item) return;
        var open = item.classList.contains('open');
        item.classList.toggle('open');
        q.setAttribute('aria-expanded', open ? 'false' : 'true');
      });
    });
  }

  // Boot
  function bootExtras() {
    setupFloatingCta();
    setupThemeToggle();
    setupFaqAccordion();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { initializeAll(); bootExtras(); });
  } else {
    initializeAll();
    bootExtras();
  }

})();
