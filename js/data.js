const appData = {
  phone: "917339284010", // WhatsApp number (no +)

  packages: [
    {
      id: "weekend-gateway",
      title: "Weekend Falls Getaway",
      price: 2499,
      duration: "2 Days / 1 Night",
      description: "A quick escape to experience the main waterfalls and relax in a comfortable stay.",
      tag: "Popular",
      category: "standard",
      image: "images/package_weekend_1776485566455.png",
      itinerary: [
        { day: 1, title: "Arrival", desc: "Check-in at 10 AM. Visit to Old Falls-Main Falls-Tiger Falls-Five Falls." },
        { day: 2, title: "Old Falls & Departure", desc: "Visit to Old Courtallam Falls. Check-out by 12 PM." }
      ],
      inclusions: ["Standard Room (1 Night)", "Local Guide Info", "Entry fees not included"]
    },
    {
      id: "family-nature",
      title: "Family Nature Retreat",
      price: 5999,
      duration: "3 Days / 2 Nights",
      description: "Perfect for families. Includes all major falls, local farm visit, and spacious family room.",
      tag: "Family",
      category: "premium",
      image: "images/hero_waterfall_1776485432981.png",
      itinerary: [
        { day: 1, title: "Arrival & Relaxation", desc: "Check-in to Family Room. Rest and acclimatize. Evening walk to Tiger Falls." },
        { day: 2, title: "Full Waterfall Tour", desc: "Visit Main Falls, Five Falls, and Shenbaga Devi Temple." },
        { day: 3, title: "Farm Visit & Departure", desc: "Visit local fruit orchards. Check-out at 11 AM." }
      ],
      inclusions: ["Family Room (2 Nights)", "Farm Entry", "Entry fees not included"]
    },
    {
      id: "budget-backpack",
      title: "Budget Backpacker Trip",
      price: 1299,
      duration: "2 Days / 1 Night",
      description: "Affordable stay with easy access to the main attractions for solo travelers or friends.",
      tag: "Budget",
      category: "budget",
      image: "images/package_weekend_1776485566455.png",
      itinerary: [
        { day: 1, title: "Explore", desc: "Check-in. Free time to explore falls on your own." },
        { day: 2, title: "Departure", desc: "Morning bath at Old Falls. Check-out." }
      ],
      inclusions: ["Budget Room (1 Night)", "Entry fees not included"]
    }
  ],


  transport: [
    {
      id: "transport-auto",
      name: "Local Auto Rickshaw",
      price: 2000,
      category: "auto",
      image: "images/transport_auto.png",
      description: "Convenient and breezy way to visit local falls. All falls package covering:<br>&bull; Main Falls (Peraruvi)<br>&bull; Five Falls (Aintharuvi)<br>&bull; Old Courtallam Falls (Pazhaya Courtallam)<br>&bull; Small Falls (Chitraruvi)<br>&bull; Tiger Falls (Puliyaruvi)",
      capacity: "Up to 5 persons"
    },
    {
      id: "transport-cab",
      name: "AC Tourist Cab",
      price: 2500,
      category: "cab",
      image: "images/package_weekend_1776485566455.png",
      description: "Comfortable AC cab for a full day of sightseeing around Courtallam and nearby temples/dams. Limit: 100km or 12 hours (whichever comes first).",
      capacity: "5 seater incl driver"
    },
    {
      id: "transport-suv",
      name: "AC SUV",
      price: 3500,
      category: "cab",
      image: "images/hero_waterfall_1776485432981.png",
      description: "Spacious vehicle perfect for families. Covers all major falls, Tenkasi temple, and local spots for a full day. Limit: 100km or 12 hours (whichever comes first).",
      capacity: "7 seater (incl driver)"
    }
  ]
};
