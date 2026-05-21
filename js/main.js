// Apply daily prices from prices.js if available
if (typeof dailyPrices !== 'undefined' && typeof appData !== 'undefined') {
  ['packages', 'rooms', 'transport'].forEach(category => {
    if (appData[category]) {
      appData[category].forEach(item => {
        if (dailyPrices[item.id] !== undefined) {
          item.price = dailyPrices[item.id];
        }
      });
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  syncContactInfo();
  initNavbar();
  initScrollAnimations();
  initAccordion();
  initTabs();
  
  // Inject WhatsApp Float Button
  injectWhatsApp();

  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
  
  initSecretAdminTriggers();
});

async function syncContactInfo() {
  try {
    const res = await fetch('/api/settings');
    if (!res.ok) return;
    const settings = await res.json();
    
    const phone = settings.contact_phone || '+91 73392 84010';
    const email = settings.contact_email || 'bookings@courtallamseasons.com';
    const whatsapp = settings.whatsapp_number || '917339284010';
    
    // Update appData.phone for dynamic links
    if (typeof appData !== 'undefined') {
      appData.phone = whatsapp;
    }
    
    // Update elements with class contact-phone-text
    document.querySelectorAll('.contact-phone-text').forEach(el => {
      const icon = el.querySelector('i, [data-lucide]');
      if (icon) {
        el.innerHTML = '';
        el.appendChild(icon);
        el.appendChild(document.createTextNode(' ' + phone));
      } else {
        el.textContent = phone;
      }
    });
    
    // Update elements with class contact-email-text
    document.querySelectorAll('.contact-email-text').forEach(el => {
      const icon = el.querySelector('i, [data-lucide]');
      if (icon) {
        el.innerHTML = '';
        el.appendChild(icon);
        el.appendChild(document.createTextNode(' ' + email));
      } else {
        el.textContent = email;
      }
    });

    // Update tel: mailto: and wa.me links
    document.querySelectorAll('a[href^="tel:"]').forEach(el => {
      el.href = `tel:${phone.replace(/\s+/g, '')}`;
    });
    document.querySelectorAll('a[href^="mailto:"]').forEach(el => {
      el.href = `mailto:${email}`;
    });
    document.querySelectorAll('a[href*="wa.me"]').forEach(el => {
      try {
        const url = new URL(el.href);
        const text = url.searchParams.get('text') || '';
        el.href = `https://wa.me/${whatsapp}${text ? '?text=' + encodeURIComponent(text) : ''}`;
      } catch (e) {
        el.href = `https://wa.me/${whatsapp}`;
      }
    });
    
    // Update the WhatsApp floating button
    const oldWaFloat = document.querySelector('.wa-float');
    if (oldWaFloat) {
      oldWaFloat.href = `https://wa.me/${whatsapp}`;
    }
  } catch (e) {
    console.error('Error syncing contact settings:', e);
  }
}

function initSecretAdminTriggers() {
  // 1. Keyboard Shortcut: Ctrl + Shift + A
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      window.location.href = '/tourver-admin-access';
    }
  });

  // 2. Logo 5 Clicks (within 5 seconds)
  const logo = document.querySelector('.nav-brand');
  if (logo) {
    let clickCount = 0;
    let clickTimer = null;
    logo.addEventListener('click', (e) => {
      e.preventDefault();
      clickCount++;
      
      if (clickCount === 1) {
        clickTimer = setTimeout(() => {
          if (clickCount < 3) {
            window.location.href = logo.getAttribute('href') || 'index.html';
          }
          clickCount = 0;
        }, 1500);
      } else if (clickCount === 3) {
        clickCount = 0;
        clearTimeout(clickTimer);
        window.location.href = '/tourver-admin-access';
      }
    });
  }
}

function initNavbar() {
  const mobileToggle = document.querySelector('.mobile-toggle');
  const navLinks = document.querySelector('.nav-links');

  if (mobileToggle && navLinks) {
    mobileToggle.addEventListener('click', () => {
      navLinks.classList.toggle('active');
    });
  }

  // Navbar Hide on Scroll
  let lastScroll = 0;
  const navbar = document.querySelector('.navbar');
  
  window.addEventListener('scroll', () => {
    const currentScroll = window.pageYOffset;
    
    if (currentScroll <= 0) {
      navbar.classList.remove('nav-hidden');
      return;
    }
    
    if (currentScroll > lastScroll && currentScroll > 80 && !navbar.classList.contains('nav-hidden')) {
      // scroll down
      navbar.classList.add('nav-hidden');
      if (navLinks) navLinks.classList.remove('active'); // Close mobile menu
    } else if (currentScroll < lastScroll && navbar.classList.contains('nav-hidden')) {
      // scroll up
      navbar.classList.remove('nav-hidden');
    }
    lastScroll = currentScroll;
  });
}

// Pricing Utility
function getCurrentPrice(item) {
  const day = new Date().getDay(); // 0=Sun, 1=Mon, ..., 6=Sat
  const isWeekend = (day === 0 || day === 6);
  
  let currentPrice = item.price;
  let originalPrice = item.original_price;

  if (isWeekend && item.weekend_price) {
    currentPrice = item.weekend_price;
    originalPrice = item.weekend_original_price || item.weekday_original_price;
  } else if (item.weekday_price) {
    currentPrice = item.weekday_price;
    originalPrice = item.weekday_original_price;
  }

  return {
    price: currentPrice,
    original: originalPrice
  };
}

function initScrollAnimations() {
  const observerOptions = {
    root: null,
    rootMargin: '0px',
    threshold: 0.1
  };

  const observer = new IntersectionObserver((entries, observer) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, observerOptions);

  document.querySelectorAll('.fade-in').forEach(element => {
    observer.observe(element);
  });
}

function initAccordion() {
  const accordions = document.querySelectorAll('.accordion-item');
  accordions.forEach(acc => {
    const header = acc.querySelector('.accordion-header');
    if (header) {
      header.addEventListener('click', () => {
        const isActive = acc.classList.contains('active');
        // Close all
        accordions.forEach(a => a.classList.remove('active'));
        // Open clicked if it wasn't active
        if (!isActive) {
          acc.classList.add('active');
        }
      });
    }
  });
}

function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanes = document.querySelectorAll('.tab-pane');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.target;
      
      // Update buttons
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      // Update panes
      tabPanes.forEach(pane => {
        if (pane.id === target || target === 'all') {
          pane.style.display = 'block';
          setTimeout(() => pane.classList.add('active'), 10);
        } else {
          pane.classList.remove('active');
          setTimeout(() => pane.style.display = 'none', 300);
        }
      });
    });
  });
}

function injectWhatsApp() {
  if (!appData.phone) return;
  const a = document.createElement('a');
  a.href = `https://wa.me/${appData.phone}`;
  a.target = '_blank';
  a.className = 'wa-float';
  a.innerHTML = '<i data-lucide="message-circle"></i>';
  document.body.appendChild(a);
  
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
}

function generateWhatsAppLink(message) {
  const encodedMessage = encodeURIComponent(message);
  return `https://wa.me/${appData.phone}?text=${encodedMessage}`;
}

// Global helper for card slider navigation
function changeCardImage(event, delta) {
  event.preventDefault();
  event.stopPropagation();
  const btn = event.currentTarget;
  const wrapper = btn.closest('.card-img-wrapper');
  const slides = wrapper.querySelectorAll('.card-slide');
  if (slides.length <= 1) return;
  
  let current = Array.from(slides).findIndex(s => s.classList.contains('active'));
  slides[current].classList.remove('active');
  current = (current + delta + slides.length) % slides.length;
  slides[current].classList.add('active');
}

// Utility to render cards
function createPackageCard(pkg) {
  const pricing = getCurrentPrice(pkg);
  let priceHTML = `₹${pricing.price}`;
  if (pkg.is_offer && pricing.original && pricing.original > pricing.price) {
    priceHTML = `
      <div style="display: flex; flex-direction: column; gap: 0.25rem;">
        <span style="text-decoration: line-through; color: var(--text-light); font-size: 0.9rem;">₹${pricing.original}</span>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span style="color: var(--primary); font-weight: 700; font-size: 1.5rem;">₹${pricing.price}</span>
        </div>
      </div>
    `;
  }

  const badgeHTML = pkg.tag ? `<span class="card-badge">${pkg.tag}</span>` : '';
  
  let images = [];
  try {
    images = pkg.images ? (typeof pkg.images === 'string' ? JSON.parse(pkg.images) : pkg.images) : [pkg.image];
  } catch(e) { images = [pkg.image]; }
  if (images.length === 0 && pkg.image) images = [pkg.image];

  let imgHTML = `<img src="${images[0]}" alt="${pkg.title}" class="card-img">`;

  return `
    <a href="package-detail.html?id=${pkg.id}" class="card fade-in" style="text-decoration: none; color: inherit; display: flex; flex-direction: column;">
      <div class="card-img-wrapper">
        ${badgeHTML}
        ${imgHTML}
      </div>
      <div class="card-body">
        <h3 class="card-title">${pkg.title}</h3>
        <div class="card-meta">
          <i data-lucide="clock"></i> ${pkg.duration}
        </div>
        <p>${pkg.description}</p>
        <div class="card-price">
          ${priceHTML} <span>/ package</span>
        </div>
        <span class="btn btn-primary mt-auto">View Details</span>
      </div>
    </a>
  `;
}

function createRoomCard(room) {
  const availabilityHTML = room.available 
    ? `<span style="color: var(--success); font-weight: 600; font-size: 0.875rem;"><i data-lucide="check-circle" style="width: 16px; height: 16px; vertical-align: middle;"></i> Available</span>`
    : `<span style="color: var(--error); font-weight: 600; font-size: 0.875rem;"><i data-lucide="x-circle" style="width: 16px; height: 16px; vertical-align: middle;"></i> Sold Out</span>`;

  const badgeHTML = room.matchScore && room.matchScore >= 80 
    ? `<div class="card-badge" style="background: var(--primary); padding: 0.3rem 0.75rem; display: flex; align-items: center; gap: 0.25rem; font-size: 0.8rem;"><i data-lucide="sparkles" style="width: 14px;"></i> Top Match</div>` 
    : '';

  const pricing = getCurrentPrice(room);
  let priceHTML = `₹${pricing.price}`;
  if (room.is_offer && pricing.original && pricing.original > pricing.price) {
    priceHTML = `
      <div style="display: flex; flex-direction: column; gap: 0.25rem;">
        <span style="text-decoration: line-through; color: var(--text-light); font-size: 0.9rem;">₹${pricing.original}</span>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span style="color: var(--primary); font-weight: 700; font-size: 1.5rem;">₹${pricing.price}</span>
        </div>
      </div>
    `;
  }

  let images = [];
  try {
    images = room.images ? (typeof room.images === 'string' ? JSON.parse(room.images) : room.images) : [room.image];
  } catch(e) { images = [room.image]; }
  if (images.length === 0 && room.image) images = [room.image];

  let imgHTML = `<img src="${images[0]}" alt="${room.name}" class="card-img">`;

  const targetUrl = `room-detail.html?id=${room.id}${window.location.search ? '&' + window.location.search.substring(1) : ''}`;
  const disabledAttr = !room.available ? 'style="opacity: 0.5; pointer-events: none;"' : '';
  const btnClass = !room.available ? 'disabled' : '';

  return `
    <a href="${room.available ? targetUrl : '#'}" class="card fade-in" style="text-decoration: none; color: inherit; display: flex; flex-direction: column; ${!room.available ? 'pointer-events: none; opacity: 0.85;' : ''}">
      <div class="card-img-wrapper">
        ${badgeHTML}
        ${imgHTML}
      </div>
      <div class="card-body">
        <h3 class="card-title">${room.name}</h3>
        <div class="mb-2" style="display: flex; align-items: center; justify-content: space-between;">
          ${availabilityHTML}
          ${room.capacity ? `<span style="color: var(--text-light); font-size: 0.875rem; font-weight: 500;"><i data-lucide="users" style="width: 16px; height: 16px; vertical-align: middle;"></i> ${room.capacity}</span>` : ''}
        </div>
        <p>${room.description}</p>
        <div class="card-meta mb-3" style="flex-wrap: wrap;">
          ${room.amenities.map(a => `<span style="background: var(--bg); padding: 0.25rem 0.5rem; border-radius: var(--radius-sm); border: 1px solid var(--border);">${a}</span>`).join('')}
        </div>
        <div class="card-price" style="margin-top: auto; margin-bottom: 1rem;">
          ${priceHTML} <span>/ night</span>
        </div>
        <span class="btn btn-primary w-100 ${btnClass}" ${disabledAttr}>Book Now</span>
      </div>
    </a>
  `;
}



function createTransportCard(transport) {
  const pricing = getCurrentPrice(transport);
  let priceHTML = `₹${pricing.price}`;
  if (transport.is_offer && pricing.original && pricing.original > pricing.price) {
    priceHTML = `
      <div style="display: flex; flex-direction: column; gap: 0.25rem;">
        <span style="text-decoration: line-through; color: var(--text-light); font-size: 0.9rem;">₹${pricing.original}</span>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span style="color: var(--primary); font-weight: 700; font-size: 1.5rem;">₹${pricing.price}</span>
        </div>
      </div>
    `;
  }

  let badgeHTML = `<span class="card-badge">${transport.category.toUpperCase()}</span>`;

  return `
    <div class="card fade-in" tabindex="0" role="button" onclick="openBookingModal('${transport.id}', '${transport.name.replace(/'/g, "\\'")}')" onkeydown="if(event.key === 'Enter') openBookingModal('${transport.id}', '${transport.name.replace(/'/g, "\\'")}')" style="display: flex; flex-direction: column;">
      <div class="card-img-wrapper">
        ${badgeHTML}
        <img src="${transport.image}" alt="${transport.name}" class="card-img">
      </div>
      <div class="card-body">
        <h3 class="card-title">${transport.name}</h3>
        <div class="card-meta mb-2">
          <i data-lucide="users"></i> ${transport.capacity}
        </div>
        <p>${transport.description}</p>
        <div class="card-price" style="margin-top: auto; margin-bottom: 1rem;">
          ${priceHTML} <span>/ trip</span>
        </div>
        <span class="btn btn-primary w-100 mt-auto">Book Transport</span>
      </div>
    </div>
  `;
}
