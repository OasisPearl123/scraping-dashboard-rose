import React, { useState, useEffect } from 'react';
import SellerTable from './SellerTable';
import UserManagement from './UserManagement';
import { supabase } from '../lib/supabase';
import * as XLSX from 'xlsx';
import {
  LogOut, TrendingUp, Users, RefreshCw, Search, Square, MapPin, ChevronDown
} from 'lucide-react';
import toast, { Toaster } from 'react-hot-toast';

function Dashboard({ user, onLogout }) {
  // PERSISTENCE: Initialize states from localStorage to handle refresh
  const [activeTab, setActiveTab] = useState(localStorage.getItem('dashboard_tab') || 'data');
  const [showResults, setShowResults] = useState(localStorage.getItem('dashboard_show_results') === 'true');

  const [sellers, setSellers] = useState([]);
  const [filteredSellers, setFilteredSellers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [provinceFilter, setProvinceFilter] = useState('all');
  const [cityFilter, setCityFilter] = useState('all');
  const [sortBy, setSortBy] = useState('followers_count_desc');

  const [availableProvinces, setAvailableProvinces] = useState([]);
  const [availableCities, setAvailableCities] = useState([]);
  const [stats, setStats] = useState({ total: 0, provinces: 0, cities: 0 });
  const [trendingOnly, setTrendingOnly] = useState(false);

  const CATEGORIES = [
    "Kuliner", "Fashion", "Beauty", "Skincare",
    "Gadget", "Elektronik", "Home Living", "Jasa"
  ];
  const [engineStatus, setEngineStatus] = useState('offline');
  const [activeScraping, setActiveScraping] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedScrapeCategory, setSelectedScrapeCategory] = useState('General');
  const [showShareModal, setShowShareModal] = useState(false);
  const [shareNumber, setShareNumber] = useState('');

  // PERSISTENCE: Sync states to localStorage
  useEffect(() => {
    localStorage.setItem('dashboard_tab', activeTab);
  }, [activeTab]);

  useEffect(() => {
    localStorage.setItem('dashboard_show_results', showResults);
  }, [showResults]);

  // Pagination States
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(10);

  // Auto Logout Timer (15 Minutes)
  useEffect(() => {
    const timer = setTimeout(() => {
      handleLogout('Session Expired (15 Minutes)');
    }, 15 * 60 * 1000);

    return () => clearTimeout(timer);
  }, []);

  // Global Error Handler for WA
  useEffect(() => {
    const handleError = (event) => {
      const errorMsg = event.error?.message || event.message || "Unknown Web Error";
      const stack = event.error?.stack || "";
      let location = "General Dashboard";

      // Attempt to identify specific location from stack trace
      if (stack.includes('handleScrape')) location = "Button Scrape Logic";
      else if (stack.includes('handleLogout')) location = "Logout Logic";
      else if (stack.includes('fetchSellers')) location = "Data Fetching Logic";
      else if (stack.includes('SellerTable')) location = "Data Table Component";

      sendWANotification(`⚠️ *WEB SYSTEM CRASH*\n\n👤 *User:* ${user.username}\n📍 *Loc:* ${location}\n❌ *Error:* ${errorMsg}`);
    };

    window.addEventListener('error', handleError);
    return () => window.removeEventListener('error', handleError);
  }, [user]);

  const sendWANotification = (msg) => {
    try {
      const waUrl = import.meta.env.VITE_WA_API_URL;
      const waId = import.meta.env.VITE_WA_INSTANCE_ID;
      const waToken = import.meta.env.VITE_WA_API_TOKEN;
      const waGroup = import.meta.env.VITE_WA_GROUP_ID;

      if (waUrl && waId && waToken && waGroup) {
        fetch(`${waUrl}/waInstance${waId}/sendMessage/${waToken}`, {
          method: 'POST',
          body: JSON.stringify({ chatId: waGroup, message: msg }),
          headers: { 'Content-Type': 'application/json' }
        }).catch(() => {});
      }
    } catch (e) {}
  };

  const getSystemInfo = async () => {
    let location = "Unknown Location";
    let ip = "N/A";
    try {
      const res = await fetch('https://ipapi.co/json/');
      const data = await res.json();
      location = `${data.city}, ${data.region}, ${data.country_name}`;
      ip = data.ip;
    } catch (e) {}

    return {
      device: navigator.userAgent.substring(0, 100),
      location,
      ip
    };
  };

  const sendDetailedAlert = async (type, status) => {
    const info = await getSystemInfo();
    const message = `🔒 *SECURITY LOG: ACQUISITION-AI*\n\n👤 *User:* ${user.username}\n📝 *Event:* ${type}\n📊 *Status:* ${status}\n📍 *Loc:* ${info.location}\n🌐 *IP:* ${info.ip}\n📱 *Device:* ${info.device}`;

    // Log to DB
    await supabase.from('user_logs').insert({
      username: user.username,
      event_type: type,
      status: status,
      device_info: info.device,
      location_info: info.location,
      ip_address: info.ip
    });

    sendWANotification(message);
  };

  useEffect(() => {
    fetchSellers();
    checkEngine();
    checkActiveTasks(); // Cek tugas aktif saat load/refresh
    const interval = setInterval(() => {
      checkEngine();
      checkActiveTasks();
    }, 15000);

    const channel = supabase.channel('dashboard-realtime')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'sellers' }, () => fetchSellers())
      .on('postgres_changes', { event: '*', schema: 'public', table: 'search_queries' }, (p) => {
        if (p.new.status === 'processing' || p.new.status === 'pending') {
          setActiveScraping(p.new.query);
          setIsProcessing(true);
        } else {
          setActiveScraping(null);
          setIsProcessing(false);
        }
      })
      .subscribe();

    return () => {
      clearInterval(interval);
      supabase.removeChannel(channel);
    };
  }, []);

  const checkEngine = async () => {
    try {
      const { data } = await supabase.from('system_status').select('last_seen').eq('id', 'main_engine').single();
      if (data) {
        const diff = (new Date() - new Date(data.last_seen)) / 1000;
        setEngineStatus(diff < 60 ? 'online' : 'offline');
      }
    } catch (e) { setEngineStatus('offline'); }
  };

  const checkActiveTasks = async () => {
    const { data } = await supabase
      .from('search_queries')
      .select('query, status')
      .in('status', ['pending', 'processing'])
      .limit(1)
      .maybeSingle();

    if (data) {
      setActiveScraping(data.query);
      setIsProcessing(true);
    } else {
      setActiveScraping(null);
      setIsProcessing(false);
    }
  };

  useEffect(() => {
    let result = [...sellers];

    // Simple Filter Logic - Focus on showing data
    if (categoryFilter !== 'all') {
      result = result.filter(s => s.category === categoryFilter);
    }

    // ADVANCED LOCATION FILTERING
    if (cityFilter === 'no_location') {
      result = result.filter(s => !s.city && !s.province);
    } else {
      if (provinceFilter !== 'all') {
        result = result.filter(s => s.province === provinceFilter);
      }
      if (cityFilter !== 'all') {
        result = result.filter(s => s.city === cityFilter);
      }
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(s =>
        (s.username?.toLowerCase().includes(q)) ||
        (s.display_name?.toLowerCase().includes(q))
      );
    }

    // Followers Sorting (Priority)
    if (sortBy === 'followers_count_desc') {
      result.sort((a, b) => (Number(b.followers_count) || 0) - (Number(a.followers_count) || 0));
    } else if (sortBy === 'followers_count_asc') {
      result.sort((a, b) => (Number(a.followers_count) || 0) - (Number(b.followers_count) || 0));
    } else {
      result.sort((a, b) => (Number(b.potential_score) || 0) - (Number(a.potential_score) || 0));
    }

    if (trendingOnly) {
      result = result.filter(s => s.potential_score > 80);
    }

    setFilteredSellers(result);
    setCurrentPage(1);
  }, [searchQuery, sortBy, sellers, categoryFilter, cityFilter, provinceFilter, trendingOnly]);

  const fetchSellers = async () => {
    try {
      const { data, error } = await supabase.from('sellers').select('*').order('created_at', { ascending: false });
      if (error) throw error;
      if (data) {
        setSellers(data);

        // Calculate REALTIME statistics from actual database records
        const uniqueProvinces = [...new Set(data.map(s => s.province).filter(Boolean))];
        const uniqueCities = [...new Set(data.map(s => s.city).filter(Boolean))];

        setStats({
          total: data.length,
          provinces: uniqueProvinces.length || 38,
          cities: uniqueCities.length
        });

        setAvailableProvinces(uniqueProvinces);
        setAvailableCities(uniqueCities);
      }
    } catch (err) {
      sendWANotification(`❌ *ERROR: FETCH DATA (SELLERS)*\n\n👤 *User:* ${user.username}\n⚠️ *Detail:* ${err.message || err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleScrape = async () => {
    if (!searchQuery.trim()) {
      toast.error('Masukkan kata kunci pencarian dahulu!');
      return;
    }
    const clean = searchQuery.trim().replace('@', '');
    try {
      const { error } = await supabase.from('search_queries').upsert({ query: clean, status: 'pending' }, { onConflict: 'query' });
      if (error) throw error;

      toast.success(`Task @${clean} dikirim ke Cloud`);
      setIsProcessing(true);
      setActiveScraping(clean);
    } catch (err) {
      toast.error('Gagal mengirim perintah scrape');
      sendWANotification(`❌ *ERROR: BUTTON SCRAPE*\n\n👤 *User:* ${user.username}\n🔍 *Query:* ${clean}\n⚠️ *Detail:* ${err.message || err}`);
    }
  };

  const handleStop = async () => {
    if (!activeScraping) return;
    try {
      const { error } = await supabase
        .from('search_queries')
        .update({ status: 'cancelled' })
        .eq('query', activeScraping);

      if (error) throw error;

      toast('Engine diberhentikan', { icon: '🛑' });
      setIsProcessing(false);
      setActiveScraping(null);
    } catch (err) {
      sendWANotification(`❌ *ERROR: BUTTON STOP ENGINE*\n\n👤 *User:* ${user.username}\n⚠️ *Detail:* ${err.message || err}`);
    }
  };

  const handleLogout = async (reason = 'Manual Logout') => {
    const actualReason = typeof reason === 'string' ? reason : 'Manual Logout';
    await sendDetailedAlert('Logout', actualReason);
    onLogout();
  };

  const handleDownloadExcel = () => {
    if (filteredSellers.length === 0) {
      toast.error('Tidak ada data untuk didownload');
      return;
    }

    const dataToExport = filteredSellers.map(s => ({
      'Username': s.username,
      'Nama Display': s.display_name,
      'Followers': s.followers_count,
      'No HP': s.phone_number,
      'Kategori': s.category,
      'Provinsi': s.province,
      'Kota': s.city,
      'Potensi Skor': s.potential_score,
      'Analisis AI': s.potential_reason,
      'URL TikTok': s.tiktok_url
    }));

    const worksheet = XLSX.utils.json_to_sheet(dataToExport);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Sellers");
    XLSX.writeFile(workbook, `TikTok_Sellers_Export_${new Date().toISOString().split('T')[0]}.xlsx`);
    toast.success('Excel berhasil didownload!');
  };

  const handleShare = async () => {
    try {
      const cleanNumber = shareNumber.replace(/\D/g, '');
      if (cleanNumber.length < 10) {
        toast.error('Nomor WA tidak valid (Min. 10 digit)');
        return;
      }

      const waUrl = import.meta.env.VITE_WA_API_URL;
      const waId = import.meta.env.VITE_WA_INSTANCE_ID;
      const waToken = import.meta.env.VITE_WA_API_TOKEN;

      toast.loading('Mengirim ke WhatsApp...', { id: 'wa-share' });
      setShowShareModal(false);

      if (waUrl && waId && waToken) {
        const message = `🚀 *ACQUISITION-AI DASHBOARD*\n\nHalo! Cek database seller TikTok UMKM potensial di sini:\n🔗 ${window.location.href}\n\n_Sent via AcquisitionAI System_`;

        const res = await fetch(`${waUrl}/waInstance${waId}/sendMessage/${waToken}`, {
          method: 'POST',
          body: JSON.stringify({ chatId: `${cleanNumber}@c.us`, message }),
          headers: { 'Content-Type': 'application/json' }
        });

        if (res.ok) {
          toast.success(`Dashboard dikirim ke WA ${cleanNumber}`, { id: 'wa-share' });
          setShareNumber('');
        } else {
          throw new Error('API Send Failed');
        }
      } else {
        window.open(`https://wa.me/${cleanNumber}?text=Cek dashboard AcquisitionAI: ${window.location.href}`, '_blank');
        toast.dismiss('wa-share');
      }
    } catch (err) {
      toast.error('Gagal mengirim via API. Membuka WhatsApp Web...', { id: 'wa-share' });
      window.open(`https://wa.me/${shareNumber.replace(/\D/g, '')}?text=Cek dashboard: ${window.location.href}`, '_blank');
    }
  };

  return (
    <div className="min-h-screen bg-[#0b0d15] text-white font-['Inter']">
      <Toaster position="top-right" />

      <nav className="border-b border-white/5 bg-[#0b0d15]/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2">
              <div className="bg-indigo-600 p-2 rounded-xl shadow-lg shadow-indigo-600/20"><Users className="w-6 h-6 text-white" /></div>
              <span className="text-xl font-black tracking-tighter uppercase italic">AcquisitionAI</span>
              <span className="px-2 py-0.5 bg-indigo-500/10 text-indigo-400 text-[8px] font-black rounded-full border border-indigo-500/20 ml-2">v2.0 Hyper-Local</span>
            </div>
            <div className="hidden md:flex bg-white/5 p-1 rounded-2xl border border-white/5 ml-4">
              <button
                onClick={() => { setActiveTab('data'); setShowResults(false); }}
                className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'data' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}
              >
                Dashboard
              </button>
              {user?.role === 'admin' && (
                <button
                  onClick={() => setActiveTab('users')}
                  className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'users' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}
                >
                  User Management
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={() => setShowShareModal(true)}
              className="flex items-center gap-2 px-6 py-2.5 bg-white/5 border border-white/5 rounded-xl text-[10px] font-black uppercase text-slate-400 hover:text-white transition-all"
            >
              <RefreshCw className="w-3 h-3" /> Bagikan <ChevronDown className="w-3 h-3" />
            </button>
            <div className="w-10 h-10 bg-slate-900 border border-white/5 rounded-xl flex items-center justify-center text-slate-500 font-black text-xs cursor-pointer hover:bg-slate-800 transition-all">?</div>
            <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-white/5 rounded-xl">
              <div className={`w-1.5 h-1.5 rounded-full ${engineStatus === 'online' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`}></div>
              <span className="text-[10px] font-black uppercase text-slate-400">@{user.username}</span>
            </div>
            <button onClick={handleLogout} className="p-2.5 text-slate-500 hover:text-rose-500 transition-colors"><LogOut className="w-5 h-5" /></button>
          </div>
        </div>
      </nav>

      <main className="max-w-[1600px] mx-auto px-6 pt-12 pb-12">
        {activeTab === 'users' ? (
          <UserManagement />
        ) : (
          <div className="space-y-12">
            <div className="animate-fade-in">
              <div className="text-center mb-16 space-y-6">
                <div className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-full text-[10px] font-black uppercase text-indigo-400 tracking-widest">
                  🎯 Hyper-Local Seller Discovery - Indonesia
                </div>
                <h1 className="text-6xl lg:text-7xl font-black uppercase leading-[0.85] text-white">
                  Temukan Seller Potensial<br />
                  <span className="text-indigo-500">Hingga Tingkat Kota</span>
                </h1>
                <p className="text-slate-500 max-w-2xl mx-auto font-medium leading-relaxed text-sm">
                  Filter seller berdasarkan Provinsi dan Kota/Kabupaten untuk memetakan pasar di setiap daerah Indonesia.
                </p>

                <div className="flex flex-wrap justify-center gap-4 mt-12">
                  <StatCard value={stats.total} label="TOTAL SELLER" />
                  <StatCard value={stats.provinces} label="PROVINSI" />
                  <StatCard value={stats.cities} label="KOTA/KAB" />
                </div>
              </div>

              <div className="bg-[#12141d]/80 backdrop-blur-xl border border-white/5 rounded-[3rem] p-10 shadow-2xl relative overflow-hidden">
                <div className="absolute top-0 right-0 p-8">
                   <button onClick={() => {
                     setProvinceFilter('all'); setCityFilter('all'); setCategoryFilter('all'); setSearchQuery('');
                   }} className="px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-[10px] font-black uppercase text-slate-500 border border-white/5 transition-all transition-colors">× Reset Filter</button>
                </div>
                <div className="flex items-center gap-2 mb-10 text-slate-400">
                  <Search className="w-4 h-4" />
                  <span className="text-[10px] font-black uppercase tracking-widest">FILTER PENCARIAN HYPER-LOCAL</span>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-16">
                  {/* Wilayah */}
                  <div className="space-y-8">
                    <div className="flex items-center gap-2 text-indigo-400">
                      <MapPin className="w-4 h-4" />
                      <span className="text-xs font-black uppercase tracking-widest italic">Filter Wilayah</span>
                    </div>

                    <div className="space-y-4">
                      {[
                        { label: 'PROVINSI', value: provinceFilter, setter: setProvinceFilter, options: availableProvinces, num: 1 },
                        { label: 'KOTA / KABUPATEN', value: cityFilter, setter: setCityFilter, options: availableCities.filter(c => provinceFilter === 'all' || sellers.find(s => s.city === c)?.province === provinceFilter), num: 2 }
                      ].map((field) => (
                        <div key={field.label} className={`grid grid-cols-[30px_1fr] items-center gap-4 ${field.disabled ? 'opacity-40' : ''}`}>
                          <span className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-black border ${field.disabled ? 'bg-slate-500/20 text-slate-500 border-slate-500/20' : 'bg-emerald-500/20 text-emerald-500 border-emerald-500/20'}`}>{field.num}</span>
                          <div className="space-y-1">
                            <label className="text-[8px] font-black text-slate-500 uppercase ml-2 tracking-widest">{field.label}</label>
                            <select
                              className={`w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-xs focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold appearance-none cursor-pointer ${field.disabled ? 'cursor-not-allowed' : ''}`}
                              value={field.value}
                              onChange={e => {
                                field.setter(e.target.value);
                                if (field.num === 1) setCityFilter('all');
                              }}
                              disabled={field.disabled}
                            >
                              <option value="all">-- Semua {field.label.toLowerCase()} --</option>
                              {field.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                            </select>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Kategori & Platform */}
                  <div className="space-y-8">
                    <div className="flex items-center gap-2 text-indigo-400">
                      <TrendingUp className="w-4 h-4" />
                      <span className="text-xs font-black uppercase tracking-widest italic">Filter Kategori & Platform</span>
                    </div>

                    <div className="space-y-6">
                      <div className="space-y-2">
                        <label className="text-[8px] font-black text-slate-500 uppercase ml-2 tracking-widest">Ketik bebas: "makanan", "tiktok", "fashion"</label>
                        <div className="relative group">
                          <input
                            className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-5 text-xs font-bold focus:ring-2 focus:ring-indigo-500 outline-none text-white italic"
                            placeholder="Ketik bebas: 'makanan', 'tiktok', 'fashion'..."
                            value={searchQuery}
                            onChange={e => setSearchQuery(e.target.value)}
                          />
                          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 group-focus-within:text-indigo-500 transition-colors" />
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-3">
                        {['TikTok'].map(p => (
                          <button key={p} className="px-5 py-3 bg-indigo-600 border border-indigo-500 rounded-xl text-[10px] font-black uppercase text-white transition-all flex items-center gap-2 shadow-lg shadow-indigo-600/20">
                             <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></div> {p}
                          </button>
                        ))}
                      </div>

                      <div className="pt-4 space-y-4">
                        <span className="text-[10px] font-black uppercase text-slate-600 block tracking-widest italic">Atau pilih kategori:</span>
                        <div className="flex flex-wrap gap-2">
                          <button
                            onClick={() => setCategoryFilter('all')}
                            className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase transition-all border ${categoryFilter === 'all' ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-600/20' : 'bg-[#161922] border-white/5 text-slate-500 hover:text-white'}`}
                          >
                            🌐 Semua
                          </button>
                          {CATEGORIES.map(cat => (
                            <button
                              key={cat}
                              onClick={() => setCategoryFilter(cat)}
                              className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase transition-all border ${categoryFilter === cat ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-600/20' : 'bg-[#161922] border-white/5 text-slate-500 hover:text-white'}`}
                            >
                              📁 {cat}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="pt-6">
                        <button
                          onClick={() => {
                            setShowResults(true);
                            setTimeout(() => {
                              document.getElementById('results-section')?.scrollIntoView({ behavior: 'smooth' });
                            }, 100);
                          }}
                          className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-6 rounded-3xl transition-all shadow-2xl shadow-indigo-600/40 uppercase text-xs tracking-[0.2em] flex items-center justify-center gap-3 active:scale-[0.98]"
                        >
                          Cari Seller Potensial
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {showResults && (
              <div id="results-section" className="animate-fade-in-up border-t border-white/5 pt-16">
                <div className="flex flex-col lg:flex-row gap-6 items-center justify-between mb-12">
                  <div className="flex items-center gap-6">
                    <div className="flex items-center gap-3">
                      <div className="bg-emerald-500 p-2.5 rounded-xl shadow-lg shadow-emerald-500/20"><TrendingUp className="w-5 h-5 text-white" /></div>
                      <h2 className="text-3xl font-black italic tracking-tighter uppercase text-white">Hasil Pencarian</h2>
                    </div>
                    <div className="flex gap-2">
                      <span className="px-3 py-1.5 bg-indigo-500/10 text-indigo-400 text-[10px] font-black uppercase rounded-lg border border-indigo-500/20">{filteredSellers.length} seller</span>
                      <span className="px-3 py-1.5 bg-indigo-500/10 text-indigo-400 text-[10px] font-black uppercase rounded-lg border border-indigo-500/20">Tingkat Kota</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3 w-full lg:w-auto">
                    <div className="relative flex-1 lg:min-w-[400px]">
                      <input
                        className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-4 text-xs font-bold focus:ring-2 focus:ring-indigo-500 outline-none text-white"
                        placeholder="Cari seller, kota..."
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                      />
                      <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                    </div>

                    <button
                      onClick={() => setTrendingOnly(!trendingOnly)}
                      className={`px-8 py-4 rounded-2xl text-[10px] font-black uppercase flex items-center gap-2 transition-all border ${trendingOnly ? 'bg-gradient-to-r from-orange-500 to-rose-600 border-none text-white shadow-lg shadow-orange-500/30' : 'bg-[#161922] border-white/5 text-slate-500'}`}
                    >
                      🔥 Trending Only
                    </button>

                    <select
                      className="bg-[#161922] border border-white/5 rounded-2xl px-8 py-4 text-[10px] font-black uppercase focus:ring-2 focus:ring-indigo-500 outline-none text-slate-400 appearance-none cursor-pointer"
                      value={sortBy}
                      onChange={e => setSortBy(e.target.value)}
                    >
                      <option value="potential_score">💎 Skor Tertinggi</option>
                      <option value="followers_count_desc">📈 Follower Terbanyak</option>
                    </select>

                    <button
                      onClick={handleDownloadExcel}
                      className="px-8 py-4 bg-emerald-600 hover:bg-emerald-500 text-white rounded-2xl text-[10px] font-black uppercase flex items-center gap-2 shadow-lg shadow-emerald-600/20 transition-all"
                    >
                       ⬇️ Download Excel
                    </button>

                    <button
                      onClick={() => setShowResults(false)}
                      className="p-4 bg-white/5 hover:bg-rose-500/10 rounded-2xl transition-all text-slate-400 hover:text-rose-500 border border-white/5"
                    >
                      <Square className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {isProcessing && activeScraping && (
                  <div className="mb-12 p-8 bg-indigo-600/10 border border-indigo-500/20 rounded-[2.5rem] flex items-center justify-between shadow-xl backdrop-blur-md">
                    <div className="flex items-center gap-6">
                      <div className="relative">
                        <RefreshCw className="w-8 h-8 animate-spin text-indigo-400" />
                        <div className="absolute inset-0 bg-indigo-400/20 blur-xl animate-pulse rounded-full"></div>
                      </div>
                      <div>
                        <span className="font-black uppercase italic tracking-tight text-white text-lg block leading-none">Engine is scanning: @{activeScraping}</span>
                        <span className="text-[10px] text-indigo-300/60 font-bold uppercase tracking-[0.3em] mt-2 block">AI Agent sedang mengekstrak profil UMKM...</span>
                      </div>
                    </div>
                    <div className="px-6 py-3 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl text-[10px] font-black uppercase text-indigo-400 animate-pulse tracking-widest">Worker Online</div>
                  </div>
                )}

                <SellerTable
                  sellers={filteredSellers.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage)}
                  loading={loading}
                />

                {/* Pagination Controls */}
                {!loading && filteredSellers.length > itemsPerPage && (
                  <div className="mt-12 flex items-center justify-center gap-6">
                    <button
                      onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                      disabled={currentPage === 1}
                      className="px-8 py-4 bg-white/5 border border-white/5 rounded-2xl font-black text-[10px] uppercase tracking-widest hover:bg-white/10 disabled:opacity-30 transition-all active:scale-95"
                    >
                      Sebelumnya
                    </button>
                    <div className="flex items-center gap-3 px-6 py-3 bg-slate-900/50 rounded-2xl border border-white/5">
                      <span className="text-[10px] font-black uppercase text-slate-500">Hal</span>
                      <span className="text-xl font-black text-indigo-400 italic">{currentPage}</span>
                      <span className="text-[10px] font-black uppercase text-slate-500">dari {Math.ceil(filteredSellers.length / itemsPerPage)}</span>
                    </div>
                    <button
                      onClick={() => setCurrentPage(prev => Math.min(prev + 1, Math.ceil(filteredSellers.length / itemsPerPage)))}
                      disabled={currentPage === Math.ceil(filteredSellers.length / itemsPerPage)}
                      className="px-8 py-4 bg-white/5 border border-white/5 rounded-2xl font-black text-[10px] uppercase tracking-widest hover:bg-white/10 disabled:opacity-30 transition-all active:scale-95"
                    >
                      Selanjutnya
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </main>

      {/* SHARE MODAL ELEGANT */}
      {showShareModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-6 animate-fade-in">
          <div className="absolute inset-0 bg-[#0b0d15]/90 backdrop-blur-md" onClick={() => setShowShareModal(false)}></div>
          <div className="bg-[#12141d] border border-white/10 w-full max-w-[400px] rounded-[2.5rem] p-10 shadow-2xl relative z-10 animate-fade-in-up">
            <div className="text-center space-y-4 mb-8">
               <div className="inline-flex p-4 bg-emerald-500/10 rounded-2xl border border-emerald-500/20">
                 <RefreshCw className="w-8 h-8 text-emerald-500" />
               </div>
               <div className="space-y-1">
                 <h3 className="text-xl font-black italic uppercase tracking-tight">Bagikan Dashboard</h3>
                 <p className="text-slate-500 text-[10px] font-bold uppercase tracking-widest leading-relaxed">Masukkan nomor WhatsApp untuk mengirim link akses secara otomatis</p>
               </div>
            </div>

            <div className="space-y-6">
              <div className="space-y-3">
                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-2">Nomor WhatsApp</label>
                <input
                  type="text"
                  placeholder="Contoh: 628123456789"
                  value={shareNumber}
                  onChange={(e) => setShareNumber(e.target.value)}
                  className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-5 text-xl font-black text-emerald-400 placeholder:text-slate-800 outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all"
                  autoFocus
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                 <button
                   onClick={() => {
                     navigator.clipboard.writeText(window.location.href);
                     toast.success('Link disalin!');
                     setShowShareModal(false);
                   }}
                   className="py-4 bg-white/5 hover:bg-white/10 border border-white/5 rounded-2xl text-[10px] font-black uppercase text-slate-400 transition-all"
                 >
                   Salin Link
                 </button>
                 <button
                   onClick={handleShare}
                   disabled={shareNumber.length < 10}
                   className="py-4 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-30 disabled:cursor-not-allowed rounded-2xl text-[10px] font-black uppercase text-white shadow-xl shadow-emerald-600/20 transition-all"
                 >
                   Kirim Sekarang
                 </button>
              </div>

              <button
                onClick={() => setShowShareModal(false)}
                className="w-full text-[10px] font-black uppercase text-slate-700 hover:text-slate-500 transition-colors pt-2"
              >
                Batal
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ value, label }) {
  return (
    <div className="bg-[#12141d] border border-white/5 rounded-3xl p-8 min-w-[200px] shadow-xl hover:bg-white/[0.02] transition-all group">
      <div className="text-4xl font-black text-white italic tracking-tighter mb-2 group-hover:text-indigo-400 transition-colors">
        {value?.toLocaleString() || 0}
      </div>
      <div className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">
        {label}
      </div>
    </div>
  );
}

export default Dashboard;
