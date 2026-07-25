import React, { useState, useEffect } from 'react';
import SellerTable from './SellerTable';
import UserManagement from './UserManagement';
import { supabase } from '../lib/supabase';
import {
  LogOut, TrendingUp, Users, RefreshCw, Search, Square, MapPin
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
  const [districtFilter, setDistrictFilter] = useState('all');
  const [sortBy, setSortBy] = useState('followers_count_desc');

  const [availableProvinces, setAvailableProvinces] = useState([]);
  const [availableCities, setAvailableCities] = useState([]);
  const [availableDistricts, setAvailableDistricts] = useState([]);
  const [stats, setStats] = useState({ total: 0, cities: 0, categories: 8 });

  const CATEGORIES = [
    "Kuliner", "Fashion", "Beauty", "Skincare",
    "Gadget", "Elektronik", "Home Living", "Jasa"
  ];
  const [engineStatus, setEngineStatus] = useState('offline');
  const [activeScraping, setActiveScraping] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedScrapeCategory, setSelectedScrapeCategory] = useState('General');

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
      result = result.filter(s => !s.city && !s.district && !s.province);
    } else {
      if (provinceFilter !== 'all') {
        result = result.filter(s => s.province === provinceFilter);
      }
      if (cityFilter !== 'all') {
        result = result.filter(s => s.city === cityFilter);
      }
    }

    if (districtFilter !== 'all') {
      result = result.filter(s => s.district === districtFilter);
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

    setFilteredSellers(result);
    setCurrentPage(1);
  }, [searchQuery, sortBy, sellers, categoryFilter, cityFilter, districtFilter]);

  const fetchSellers = async () => {
    try {
      const { data, error } = await supabase.from('sellers').select('*').order('created_at', { ascending: false });
      if (error) throw error;
      if (data) {
        setSellers(data);

        // Always show the current data count
        setStats({
          total: data.length,
          cities: [...new Set(data.map(s => s.city).filter(c => c))].length,
          categories: 8
        });

        setAvailableProvinces([...new Set(data.map(s => s.province).filter(p => p))]);
        setAvailableCities([...new Set(data.map(s => s.city).filter(c => c))]);
        setAvailableDistricts([...new Set(data.map(s => s.district).filter(d => d))]);
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

  const handleLogout = (reason = 'Manual Logout') => {
    const actualReason = typeof reason === 'string' ? reason : 'Manual Logout';
    const status = actualReason.includes('Expired') ? '🔒 AUTO LOGOUT (15 MIN)' : '🔒 MANUAL LOGOUT';
    const msg = `${status}\n\n👤 *User:* ${user.username}\n⏰ *Time:* ${new Date().toLocaleString('id-ID')}\n📝 *Reason:* ${actualReason}`;
    sendWANotification(msg);
    onLogout();
  };

  return (
    <div className="min-h-screen bg-[#0b0d15] text-white font-['Inter']">
      <Toaster position="top-right" />

      <nav className="border-b border-white/5 bg-[#0b0d15]/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2">
              <div className="bg-indigo-600 p-2 rounded-xl"><Users className="w-6 h-6 text-white" /></div>
              <span className="text-xl font-black tracking-tighter uppercase italic">AcquisitionAI</span>
            </div>
            <div className="hidden md:flex bg-white/5 p-1 rounded-2xl border border-white/5">
              <button onClick={() => { setActiveTab('data'); setShowResults(false); }} className={`px-6 py-2 rounded-xl text-xs font-black uppercase transition-all ${activeTab === 'data' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-white'}`}>Data Dashboard</button>
              {user?.role === 'admin' && (
                <button onClick={() => setActiveTab('users')} className={`px-6 py-2 rounded-xl text-xs font-black uppercase transition-all ${activeTab === 'users' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-white'}`}>Manage Users</button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-white/5 rounded-xl">
              <div className={`w-1.5 h-1.5 rounded-full ${engineStatus === 'online' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`}></div>
              <span className="text-[10px] font-black uppercase text-slate-400">@{user.username} ({user.role})</span>
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
              {/* HERO SECTION BASED ON IMAGE */}
              <div className="text-center mb-16 space-y-6">
                <div className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-full text-[10px] font-black uppercase text-indigo-400 tracking-widest animate-pulse">
                  🚀 Hyper-Local Seller Discovery - Indonesia
                </div>
                <h1 className="text-5xl lg:text-6xl font-black italic tracking-tighter uppercase leading-[0.9]">
                  Temukan Seller Potensial<br />
                  <span className="text-indigo-500">Hingga Tingkat Kota</span>
                </h1>
                <p className="text-slate-500 max-w-2xl mx-auto font-medium leading-relaxed">
                  Filter seller TikTok UMKM berdasarkan wilayah dan kategori secara realtime untuk memetakan pasar di setiap daerah Indonesia.
                </p>

                {/* STATS CARDS */}
                <div className="flex flex-wrap justify-center gap-6 mt-12">
                  <StatCard value={stats.total} label="TOTAL SELLER" />
                  <StatCard value={38} label="PROVINSI" />
                  <StatCard value={stats.cities} label="KOTA/KAB" />
                  <StatCard value={stats.categories} label="KATEGORI" />
                </div>
              </div>

              {/* FILTER BOX BASED ON IMAGE */}
              <div className="bg-[#12141d] border border-white/5 rounded-[2.5rem] p-8 mb-12 shadow-2xl">
                <div className="flex items-center gap-2 mb-8 text-slate-400">
                  <Search className="w-4 h-4" />
                  <span className="text-[10px] font-black uppercase tracking-widest">Filter Pencarian Hyper-Local</span>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
                  {/* Wilayah */}
                  <div className="space-y-6">
                    <div className="flex items-center gap-2 text-indigo-400">
                      <MapPin className="w-4 h-4" />
                      <span className="text-xs font-bold uppercase">Filter Wilayah</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      <div className="space-y-2">
                        <label className="text-[10px] font-bold text-slate-500 uppercase ml-2">Provinsi</label>
                        <select
                          className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-sm focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold"
                          value={provinceFilter}
                          onChange={e => {
                              setProvinceFilter(e.target.value);
                              setCityFilter('all');
                          }}
                        >
                          <option value="all">Semua Provinsi</option>
                          {availableProvinces.map(p => <option key={p} value={p}>{p}</option>)}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] font-bold text-slate-500 uppercase ml-2">Kota/Kabupaten</label>
                        <select
                          className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-sm focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold"
                          value={cityFilter}
                          onChange={e => setCityFilter(e.target.value)}
                        >
                          <option value="all">Semua Kota</option>
                          <option value="no_location" className="text-indigo-400">⚠️ Data Tanpa Wilayah</option>
                          {availableCities.filter(c => {
                              if (provinceFilter === 'all') return true;
                              const seller = sellers.find(s => s.city === c);
                              return seller?.province === provinceFilter;
                          }).map(city => <option key={city} value={city}>{city}</option>)}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] font-bold text-slate-500 uppercase ml-2">Kecamatan</label>
                        <select
                          className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-sm focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold"
                          value={districtFilter}
                          onChange={e => setDistrictFilter(e.target.value)}
                          disabled={cityFilter === 'no_location'}
                        >
                          <option value="all">Semua Kecamatan</option>
                          {availableDistricts.map(dist => <option key={dist} value={dist}>{dist}</option>)}
                        </select>
                      </div>
                    </div>
                  </div>

                  {/* Kategori */}
                  <div className="space-y-6">
                    <div className="flex items-center gap-2 text-indigo-400">
                      <TrendingUp className="w-4 h-4" />
                      <span className="text-xs font-bold uppercase">Kategori & Platform</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => setCategoryFilter('all')}
                        className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase transition-all border ${categoryFilter === 'all' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-[#161922] border-white/5 text-slate-500 hover:text-white'}`}
                      >
                        Semua
                      </button>
                      {CATEGORIES.map(cat => (
                        <button
                          key={cat}
                          onClick={() => setCategoryFilter(cat)}
                          className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase transition-all border ${categoryFilter === cat ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-[#161922] border-white/5 text-slate-500 hover:text-white'}`}
                        >
                          {cat}
                        </button>
                      ))}
                    </div>
                    <div className="flex items-center justify-between gap-4 pt-4">
                      <div className="px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-xl text-[10px] font-black text-indigo-400 uppercase">TikTok Only</div>
                      <button
                        onClick={() => {
                          setShowResults(true);
                          setTimeout(() => {
                            document.getElementById('results-section')?.scrollIntoView({ behavior: 'smooth' });
                          }, 100);
                        }}
                        className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white font-black py-5 rounded-2xl transition-all shadow-xl shadow-indigo-600/30 uppercase text-xs tracking-widest flex items-center justify-center gap-3 active:scale-[0.98]"
                      >
                        <Search className="w-4 h-4" /> Cari Seller Potensial
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {showResults && (
              <div id="results-section" className="animate-fade-in-up border-t border-white/5 pt-12">
                <div className="flex flex-col lg:flex-row gap-6 items-center justify-between mb-12">
                  <div className="flex items-center gap-4">
                    <div className="bg-indigo-600 p-3 rounded-2xl shadow-lg shadow-indigo-600/20"><TrendingUp className="w-6 h-6 text-white" /></div>
                    <div>
                      <h2 className="text-2xl font-black italic tracking-tighter uppercase leading-none">Hasil Analisis UMKM</h2>
                      <span className="text-indigo-400 text-[10px] font-black uppercase tracking-[0.2em]">Ditemukan {filteredSellers.length} Data Potensial</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-3 w-full lg:w-auto">
                    <select
                      className="bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-[10px] font-black uppercase focus:ring-2 focus:ring-indigo-500 outline-none text-slate-400"
                      value={sortBy}
                      onChange={e => setSortBy(e.target.value)}
                    >
                      <option value="followers_count_desc">📈 Follower Terbanyak</option>
                      <option value="followers_count_asc">📉 Follower Terendah</option>
                      <option value="potential_score">💎 Skor Tertinggi</option>
                    </select>
                    <div className="relative flex-1 lg:min-w-[300px]">
                      <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                      <input
                        className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-4 text-xs font-bold focus:ring-2 focus:ring-indigo-500 outline-none text-white"
                        placeholder="Filter pencarian di sini..."
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        disabled={isProcessing}
                      />
                    </div>
                    {isProcessing ? (
                      <button onClick={handleStop} className="bg-rose-600 hover:bg-rose-500 px-8 rounded-2xl text-xs font-black transition-all flex items-center gap-2 uppercase tracking-widest">
                        <Square className="w-3 h-3 fill-current" /> STOP
                      </button>
                    ) : (
                      <button onClick={handleScrape} className="bg-indigo-600 hover:bg-indigo-500 px-8 py-4 rounded-2xl text-xs font-black transition-all shadow-lg shadow-indigo-500/20 uppercase tracking-widest">Scrape</button>
                    )}
                    <button
                      onClick={() => setShowResults(false)}
                      className="p-4 bg-white/5 hover:bg-rose-500/10 rounded-2xl transition-all text-slate-400 hover:text-rose-500 border border-white/5"
                      title="Sembunyikan Hasil"
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
