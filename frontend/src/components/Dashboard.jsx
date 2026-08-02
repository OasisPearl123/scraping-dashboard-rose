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
  const [activeTab, setActiveTab] = useState(localStorage.getItem('dashboard_tab') || 'data');
  const [showResults, setShowResults] = useState(localStorage.getItem('dashboard_show_results') === 'true');
  const [sellers, setSellers] = useState([]);
  const [filteredSellers, setFilteredSellers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [provinceFilter, setProvinceFilter] = useState('all');
  const [cityFilter, setCityFilter] = useState('all');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [sortBy, setSortBy] = useState('potential_score');
  const [availableProvinces, setAvailableProvinces] = useState([]);
  const [availableCities, setAvailableCities] = useState([]);
  const [stats, setStats] = useState({ total: 0, provinces: 0, cities: 0 });
  const [trendingOnly, setTrendingOnly] = useState(false);
  const [activeScraping, setActiveScraping] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [showShareModal, setShowShareModal] = useState(false);
  const [shareNumber, setShareNumber] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(10);

  const CATEGORIES = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"];

  useEffect(() => { localStorage.setItem('dashboard_tab', activeTab); }, [activeTab]);
  useEffect(() => { localStorage.setItem('dashboard_show_results', showResults); }, [showResults]);

  useEffect(() => {
    fetchSellers();
    checkActiveTasks();
    const interval = setInterval(() => { checkActiveTasks(); }, 15000);

    // 🔥 REALTIME: Subscribe to sellers table for instant updates
    const channel = supabase.channel('dashboard-realtime')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'sellers' },
        () => fetchSellers()
      )
      .subscribe();

    return () => {
      clearInterval(interval);
      supabase.removeChannel(channel);
    };
  }, []);

  const checkActiveTasks = async () => {
    try {
      const { data } = await supabase.from('search_queries').select('query, status').in('status', ['pending', 'processing']).limit(1).maybeSingle();
      if (data) { setActiveScraping(data.query); setIsProcessing(true); } else { setActiveScraping(null); setIsProcessing(false); }
    } catch (e) { console.error(e); }
  };

  useEffect(() => {
    let result = [...sellers];

    // Platform Filter
    if (platformFilter !== 'all') {
      result = result.filter(s => (s.platform || 'tiktok').toLowerCase() === platformFilter.toLowerCase());
    }

    if (categoryFilter !== 'all') result = result.filter(s => s.category === categoryFilter);
    if (cityFilter === 'no_location') {
      result = result.filter(s => !s.city && !s.province);
    } else {
      if (provinceFilter !== 'all') result = result.filter(s => s.province === provinceFilter);
      if (cityFilter !== 'all') result = result.filter(s => s.city === cityFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(s => s.username?.toLowerCase().includes(q) || s.display_name?.toLowerCase().includes(q));
    }
    if (sortBy === 'followers_count_desc') result.sort((a, b) => (Number(b.followers_count) || 0) - (Number(a.followers_count) || 0));
    else result.sort((a, b) => (Number(b.potential_score) || 0) - (Number(a.potential_score) || 0));
    if (trendingOnly) result = result.filter(s => s.potential_score > 80);

    setFilteredSellers(result);
    setCurrentPage(1);
  }, [searchQuery, sortBy, sellers, categoryFilter, cityFilter, provinceFilter, trendingOnly, platformFilter]);

  const fetchSellers = async () => {
    try {
      setLoading(true);
      // Fetching from 'sellers' table as requested
      const { data, error, count } = await supabase
        .from('sellers')
        .select('*', { count: 'exact' })
        .order('created_at', { ascending: false })
        .limit(10000);

      if (error) throw error;
      if (data) {
        setSellers(data);

        const uniqueProvinces = [...new Set(data.map(s => s.province).filter(Boolean))];
        const uniqueCities = [...new Set(data.map(s => s.city).filter(Boolean))];

        setStats({
          total: count || data.length,
          provinces: uniqueProvinces.length > 0 ? uniqueProvinces.length : 38,
          cities: uniqueCities.length
        });

        setAvailableProvinces(uniqueProvinces);
        setAvailableCities(uniqueCities);
      }
    } catch (err) { console.error(err); } finally { setLoading(false); }
  };

  const handleScrape = async () => {
    if (!searchQuery.trim()) { toast.error('Masukkan kata kunci pencarian dahulu!'); return; }
    const clean = searchQuery.trim().replace('@', '');
    try {
      await supabase.from('search_queries').upsert({ query: clean, status: 'pending' }, { onConflict: 'query' });
      toast.success(`Tugas @${clean} dikirim ke Cloud`);
      setIsProcessing(true); setActiveScraping(clean);
    } catch (err) { toast.error('Gagal mengirim perintah scrape'); }
  };

  const handleLogout = () => { onLogout(); };

  const handleDownloadExcel = () => {
    if (filteredSellers.length === 0) { toast.error('Tidak ada data untuk didownload'); return; }
    const dataToExport = filteredSellers.map(s => ({ 'Username': s.username, 'Display Name': s.display_name, 'Followers': s.followers_count, 'No HP': s.phone_number, 'Kategori': s.category, 'Provinsi': s.province, 'Kota': s.city, 'Potensi Skor': s.potential_score, 'Analisis AI': s.potential_reason }));
    const ws = XLSX.utils.json_to_sheet(dataToExport);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Sellers");
    XLSX.writeFile(wb, `TikTok_Sellers_${new Date().toISOString().split('T')[0]}.xlsx`);
    toast.success('Excel berhasil didownload!');
  };

  const handleShare = async () => {
    try {
      const cleanNumber = shareNumber.replace(/\D/g, '');
      if (cleanNumber.length < 10) { toast.error('Nomor WA tidak valid'); return; }
      const dashboardUrl = "https://scraping-dashboard-rose.vercel.app/";
      setShowShareModal(false);
      window.open(`https://wa.me/${cleanNumber}?text=Cek dashboard: ${dashboardUrl}`, '_blank');
    } catch (err) { console.error(err); }
  };

  return (
    <div className="min-h-screen bg-[#0b0d15] text-white font-['Inter']">
      <Toaster position="top-right" />

      <nav className="border-b border-white/5 bg-[#0b0d15]/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2">
              <div className="bg-indigo-600 p-2 rounded-xl shadow-lg shadow-indigo-600/20"><Users className="w-6 h-6 text-white" /></div>
              <span className="text-xl font-black tracking-tighter uppercase">Acquisition</span>
              <span className="px-2 py-0.5 bg-indigo-500/10 text-indigo-400 text-[8px] font-black rounded-full border border-indigo-500/20 ml-2">v2.0 Hyper-Local</span>
            </div>
            <div className="hidden md:flex bg-white/5 p-1 rounded-2xl border border-white/5 ml-4">
              <button onClick={() => { setActiveTab('data'); setShowResults(false); }} className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'data' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}>Dashboard</button>
              {user?.role === 'admin' && (
                <button onClick={() => setActiveTab('users')} className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'users' ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}>User Management</button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button onClick={() => setShowShareModal(true)} className="flex items-center gap-2 px-6 py-2.5 bg-white/5 border border-white/5 rounded-xl text-[10px] font-black uppercase text-slate-400 hover:text-white transition-all"><RefreshCw className="w-3 h-3" /> Bagikan <ChevronDown className="w-3 h-3" /></button>
            <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-white/5 rounded-xl">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></div>
              <span className="text-[10px] font-black uppercase text-slate-400">System Active</span>
            </div>
            <button onClick={handleLogout} className="p-2.5 text-slate-500 hover:text-rose-500 transition-colors"><LogOut className="w-5 h-5" /></button>
          </div>
        </div>
      </nav>

      <main className="max-w-[1600px] mx-auto px-6 pt-12 pb-12">
        {activeTab === 'users' ? <UserManagement /> : (
          <div className="space-y-12">
            <div className="text-center space-y-6 mb-16">
              <div className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-full text-[10px] font-black uppercase text-indigo-400 tracking-widest">🎯 Hyper-Local Seller Discovery - Indonesia</div>
              <h1 className="text-6xl lg:text-7xl font-black uppercase leading-[0.85] text-white">Temukan Seller Potensial<br /><span className="text-indigo-500">Hingga Tingkat Kota</span></h1>
              <p className="text-slate-500 max-w-2xl mx-auto font-medium leading-relaxed text-sm">Filter seller berdasarkan Provinsi dan Kota/Kabupaten untuk memetakan pasar di setiap daerah Indonesia.</p>
              <div className="flex flex-wrap justify-center gap-4 mt-12">
                <StatCard value={stats.total} label="TOTAL SELLER" />
                <StatCard value={stats.provinces || 38} label="PROVINSI" />
                <StatCard value={stats.cities} label="KOTA/KAB" />
              </div>
            </div>

            <div className="bg-[#12141d]/80 backdrop-blur-xl border border-white/5 rounded-[3rem] p-10 shadow-2xl relative overflow-hidden">
                <div className="absolute top-0 right-0 p-8">
                   <button onClick={() => { setProvinceFilter('all'); setCityFilter('all'); setCategoryFilter('all'); setSearchQuery(''); }} className="px-4 py-2 bg-white/5 border border-white/5 rounded-xl text-[10px] font-black uppercase text-slate-500 transition-all">× Reset Filter</button>
                </div>
                <div className="flex items-center gap-2 mb-10 text-slate-400">
                  <Search className="w-4 h-4" />
                  <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">FILTER PENCARIAN HYPER-LOCAL</span>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-16">
                  <div className="space-y-8">
                    <div className="flex items-center gap-2 text-indigo-400 font-black uppercase text-xs italic tracking-widest">Filter Wilayah</div>
                    <div className="space-y-4">
                      {[
                        { label: 'PROVINSI', value: provinceFilter, setter: setProvinceFilter, options: availableProvinces, num: 1 },
                        { label: 'KOTA / KABUPATEN', value: cityFilter, setter: setCityFilter, options: availableCities.filter(c => provinceFilter === 'all' || sellers.find(s => s.city === c)?.province === provinceFilter), num: 2 }
                      ].map(f => (
                        <div key={f.label} className="grid grid-cols-[30px_1fr] items-center gap-4">
                          <span className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-500 flex items-center justify-center text-[10px] font-black border border-emerald-500/20">{f.num}</span>
                          <div className="space-y-1">
                            <label className="text-[8px] font-black text-slate-500 uppercase ml-2 tracking-widest">{f.label}</label>
                            <select className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-xs focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold appearance-none cursor-pointer" value={f.value} onChange={e => { f.setter(e.target.value); if(f.num===1) setCityFilter('all'); }}>
                              <option value="all">-- Semua {f.label.toLowerCase()} --</option>
                              {f.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                            </select>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-8">
                    <div className="flex items-center gap-2 text-indigo-400 font-black uppercase text-xs italic tracking-widest">Filter Kategori & Platform</div>
                    <div className="space-y-6">
                      <div className="space-y-2">
                        <label className="text-[8px] font-black text-slate-500 uppercase ml-2 tracking-widest italic">Ketik bebas: "makanan", "tiktok", "fashion"</label>
                        <div className="relative">
                          <input className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-5 text-xs font-bold focus:ring-2 focus:ring-indigo-500 outline-none text-white italic" placeholder="Ketik bebas: 'makanan', 'tiktok', 'fashion'..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
                          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-3">
                        {['TikTok', 'Instagram', 'Threads', 'Shopee'].map(p => (
                          <button
                            key={p}
                            onClick={() => setPlatformFilter(p.toLowerCase())}
                            className={`px-5 py-3 border rounded-xl text-[10px] font-black uppercase transition-all flex items-center gap-2 ${platformFilter === p.toLowerCase() ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-600/20' : 'bg-[#161922] border-white/5 text-slate-500 hover:text-white'}`}
                          >
                             {platformFilter === p.toLowerCase() && <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></div>}
                             {p}
                          </button>
                        ))}
                      </div>
                      <div className="pt-4 space-y-4">
                        <span className="text-[10px] font-black uppercase text-slate-600 block italic tracking-widest">Atau pilih kategori:</span>
                        <div className="flex flex-wrap gap-2">
                          <button onClick={() => setCategoryFilter('all')} className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase border transition-all ${categoryFilter === 'all' ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg' : 'bg-[#161922] border-white/5 text-slate-500'}`}>🌐 Semua</button>
                          {CATEGORIES.map(cat => <button key={cat} onClick={() => setCategoryFilter(cat)} className={`px-6 py-3 rounded-2xl text-[10px] font-black uppercase border transition-all ${categoryFilter === cat ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg' : 'bg-[#161922] border-white/5 text-slate-500'}`}>📁 {cat}</button>)}
                        </div>
                      </div>
                      <button onClick={() => { setShowResults(true); setTimeout(() => document.getElementById('results-section')?.scrollIntoView({ behavior: 'smooth' }), 100); }} className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-6 rounded-3xl transition-all shadow-2xl shadow-indigo-600/40 uppercase text-xs tracking-[0.2em] flex items-center justify-center gap-3 active:scale-[0.98]">Cari Seller Potensial</button>
                    </div>
                  </div>
                </div>
            </div>

            {showResults && (
              <div id="results-section" className="animate-fade-in-up border-t border-white/5 pt-16">
                <div className="flex flex-col lg:flex-row gap-6 items-center justify-between mb-12">
                  <div className="flex items-center gap-6">
                    <div className="bg-emerald-500 p-2.5 rounded-xl shadow-lg"><TrendingUp className="w-5 h-5 text-white" /></div>
                    <h2 className="text-3xl font-black uppercase text-white">Hasil Pencarian</h2>
                    <div className="flex gap-2">
                      <span className="px-3 py-1.5 bg-indigo-500/10 text-indigo-400 text-[10px] font-black uppercase rounded-lg border border-indigo-500/20">{filteredSellers.length} seller</span>
                      <span className="px-3 py-1.5 bg-indigo-500/10 text-indigo-400 text-[10px] font-black uppercase rounded-lg border border-indigo-500/20">Tingkat Kota</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-3 w-full lg:w-auto">
                    <div className="relative flex-1 lg:min-w-[400px]">
                      <input className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-4 text-xs font-bold outline-none text-white focus:ring-2 focus:ring-indigo-500" placeholder="Cari seller, kota..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
                      <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                    </div>
                    <button onClick={() => setTrendingOnly(!trendingOnly)} className={`px-8 py-4 rounded-2xl text-[10px] font-black uppercase flex items-center gap-2 transition-all ${trendingOnly ? 'bg-gradient-to-r from-orange-500 to-rose-600 text-white shadow-lg' : 'bg-[#161922] text-slate-500 border border-white/5'}`}>🔥 Trending Only</button>
                    <select className="bg-[#161922] border border-white/5 rounded-2xl px-8 py-4 text-[10px] font-black uppercase text-slate-400" value={sortBy} onChange={e => setSortBy(e.target.value)}>
                      <option value="potential_score">💎 Skor Tertinggi</option>
                      <option value="potential_score_asc">📉 Skor Terendah</option>
                      <option value="followers_count_desc">📈 Follower Terbanyak</option>
                      <option value="followers_count_asc">📉 Follower Terendah</option>
                    </select>
                    <button onClick={handleDownloadExcel} className="px-8 py-4 bg-emerald-600 text-white rounded-2xl text-[10px] font-black uppercase shadow-lg shadow-emerald-500/20">⬇️ Download Excel</button>
                    <button onClick={() => setShowResults(false)} className="p-4 bg-white/5 text-slate-400 rounded-2xl border border-white/5"><Square className="w-4 h-4" /></button>
                  </div>
                </div>


                <SellerTable sellers={filteredSellers.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage)} loading={loading} />

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

      {showShareModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
          <div className="absolute inset-0 bg-[#0b0d15]/90 backdrop-blur-md" onClick={() => setShowShareModal(false)}></div>
          <div className="bg-[#12141d] border border-white/10 w-full max-w-[400px] rounded-[2.5rem] p-10 shadow-2xl relative z-10">
            <h3 className="text-xl font-black uppercase text-white text-center mb-6">Bagikan Dashboard</h3>
            <div className="space-y-4">
              <input type="text" placeholder="Contoh: 628123456789" value={shareNumber} onChange={(e) => setShareNumber(e.target.value)} className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-5 text-xl font-black text-emerald-400 outline-none focus:ring-2 focus:ring-emerald-500" />
              <div className="grid grid-cols-2 gap-4">
                 <button onClick={() => { navigator.clipboard.writeText("https://scraping-dashboard-rose.vercel.app/"); toast.success('Link disalin!'); setShowShareModal(false); }} className="py-4 bg-white/5 border border-white/5 rounded-2xl text-[10px] font-black uppercase text-slate-400 transition-all">Salin Link</button>
                 <button onClick={handleShare} className="py-4 bg-emerald-600 rounded-2xl text-[10px] font-black uppercase text-white shadow-xl shadow-emerald-600/20">Kirim WA</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ value, label }) {
  return (
    <div className="bg-[#12141d] border border-white/5 rounded-3xl p-8 min-w-[200px] shadow-xl hover:bg-white/[0.02] transition-all">
      <div className="text-4xl font-black text-white italic tracking-tighter mb-2">{value?.toLocaleString() || 0}</div>
      <div className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">{label}</div>
    </div>
  );
}

export default Dashboard;
