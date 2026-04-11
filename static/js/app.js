// ---------- Tracker modal ----------
function openTracker()  { document.getElementById("tracker").classList.add("open"); }
function closeTracker() { document.getElementById("tracker").classList.remove("open"); }

async function saveTracker() {
  const t = window.TRACK;
  // Clamp progress before saving
  const totalMax = t.total || 0;
  let progress = parseInt(document.getElementById("t-progress").value) || 0;
  if (progress < 0) progress = 0;
  if (totalMax > 0 && progress > totalMax) progress = totalMax;

  const payload = {
    mal_id:    t.mal_id,
    title:     t.title,
    title_english: t.title_english || null,
    title_japanese: t.title_japanese || null,
    cover_url: t.cover_url,
    status:    document.getElementById("t-status").value,
    progress:  progress,
    score:     document.getElementById("t-score").value || null,
  };
  if (t.kind === "anime") payload.total_eps = t.total;
  else                    payload.total_chs = t.total;

  const url = t.kind === "anime" ? "/api/list/anime" : "/api/list/manga";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    closeTracker();
    alert("Saved to your list!");
  } else {
    alert("Failed to save.");
  }
}

// ---------- Delete from My List ----------
async function deleteEntry(kind, malId, btn) {
  if (!confirm("Remove from your list?")) return;
  const res = await fetch(`/api/list/${kind}/${malId}/delete`, { method: "POST" });
  if (res.ok) btn.closest(".row").remove();
}

// ---------- Tab Switching ----------
function switchTab(tabName) {
  // Remove active class from all tabs and content
  document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
  
  // Add active class to clicked tab and corresponding content
  event.target.classList.add('active');
  document.getElementById(`tab-${tabName}`).classList.add('active');
}

// ---------- Synopsis Toggle ----------
function toggleSynopsis() {
  const shortText = document.getElementById('synopsis-text');
  const fullText = document.getElementById('synopsis-full');
  const readLess = document.getElementById('read-less-btn');
  
  if (shortText && fullText) {
    const showing = !fullText.classList.contains('hidden');
    if (showing) {
      shortText.classList.remove('hidden');
      fullText.classList.add('hidden');
      if (shortText.nextElementSibling) shortText.nextElementSibling.classList.remove('hidden');
      if (readLess) readLess.classList.add('hidden');
    } else {
      shortText.classList.add('hidden');
      fullText.classList.remove('hidden');
      if (shortText.nextElementSibling) shortText.nextElementSibling.classList.add('hidden');
      if (readLess) readLess.classList.remove('hidden');
    }
  }
}

// ---------- Carousel Navigation ----------
let carouselPosition = 0;
const CARD_WIDTH = 180 + 16; // card width + gap
const VISIBLE_CARDS = 5;

function slideCarousel(direction) {
  const carousel = document.getElementById('carousel');
  if (!carousel) return;
  
  const totalCards = carousel.children.length;
  const maxPosition = Math.max(0, totalCards - VISIBLE_CARDS);
  
  carouselPosition += direction;
  carouselPosition = Math.max(0, Math.min(maxPosition, carouselPosition));
  
  carousel.scrollTo({
    left: carouselPosition * CARD_WIDTH,
    behavior: 'smooth'
  });
}

// ---------- Carousel Auto-Refresh ----------
let carouselTimer = 28800; // 8 hours in seconds (8 * 60 * 60)
let carouselInterval;

function startCarouselTimer() {
  if (carouselInterval) clearInterval(carouselInterval);
  
  carouselTimer = 28800; // Reset to 8 hours
  
  carouselInterval = setInterval(() => {
    carouselTimer--;
    
    if (carouselTimer <= 0) {
      refreshCarousel();
    }
  }, 1000);
}

async function refreshCarousel() {
  const carousel = document.getElementById('carousel');
  if (!carousel) return;
  
  const kind = carousel.dataset.kind;
  
  try {
    const response = await fetch(`/api/carousel/${kind}`);
    const data = await response.json();
    
    if (data.items && data.items.length > 0) {
      // Rebuild carousel HTML
      carousel.innerHTML = '';
      data.items.forEach(item => {
        const link = kind === 'anime' 
          ? `/anime/${item.mal_id}` 
          : `/manga/${item.mal_id}`;
        const title = item.title_english || item.title;
        const imgUrl = item.images.jpg.large_image_url;
        
        const card = document.createElement('a');
        card.href = link;
        card.className = 'carousel-card';
        card.innerHTML = `
          <div class="carousel-img" style="background-image: url('${imgUrl}')"></div>
          <div class="carousel-score">★ ${item.score}</div>
          <div class="carousel-overlay">
            <div class="carousel-card-title">${title}</div>
          </div>
        `;
        carousel.appendChild(card);
      });
      
      carouselPosition = 0;
      carousel.scrollTo({ left: 0 });
    }
  } catch (error) {
    console.error('Failed to refresh carousel:', error);
  }
  
  startCarouselTimer();
}

// Start carousel timer on page load
if (document.getElementById('carousel')) {
  startCarouselTimer();
}
