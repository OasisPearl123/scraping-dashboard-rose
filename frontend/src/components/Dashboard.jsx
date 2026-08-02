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
  const [showResults, setShowResults] = useState(true);
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

  useEffect(() => {
    fetchSellers();
    const interval = setInterval(() => { checkActiveTasks(); }, 15000);

    const channel = supabase.channel('dashboard-realtime')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'sellers' }, () => fetchSellers())
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

  const fetchSellers = async () => {
    try {
      setLoading(true);
      console.log("🔄 Menghubungkan ke Database Sellers...");

      // Ambil data terbaru (limit 2000 untuk stabilitas)
      const { data, error, count } = await supabase
        .from('sellers')
        .select('*', { count: 'exact' })
        .order('created_at', { ascending: false })
        .limit(2000);

      if (error) {
        console.error("❌ Supabase Error:", error);
        toast.error("Koneksi Database Gagal");
        return;
      }

      if (data) {
        console.log(`✅ Berhasil memuat ${data.length} data. Total di server: ${count}`);
        setSellers(data);

        const uniqueProvinces = [...new Set(data.map(s => s.province).filter(Boolean))];
        const uniqueCities = [...new Set(data.map(s => s.city).filter(Boolean))];

        setStats({
          total: count || data.length,
          provinces: uniqueProvinces.length > 0 ? uniqueProvinces.length : 34,
          cities: uniqueCities.length
        });

        setAvailableProvinces(uniqueProvinces);
        setAvailableCities(uniqueCities);
      }
    } catch (err) {
      console.error("❌ Fetch failed:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let result = [...sellers];

    if (platformFilter !== 'all') {
      result = result.filter(s => (s.platform || 'tiktok').toLowerCase() === platformFilter.toLowerCase());
    }

    if (categoryFilter !== 'all') result = result.filter(s => s.category === categoryFilter);

    if (provinceFilter !== 'all') result = result.filter(s => s.province === provinceFilter);
    if (cityFilter !== 'all') result = result.filter(s => s.city === cityFilter);

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(s => s.username?.toLowerCase().includes(q) || s.display_name?.toLowerCase().includes(q) || s.city?.toLowerCase().includes(q));
    }

    if (sortBy === 'followers_count_desc') result.sort((a, b) => (Number(b.followers_count) || 0) - (Number(a.followers_count) || 0));
    else if (sortBy === 'potential_score_asc') result.sort((a, b) => (Number(a.potential_score) || 0) - (Number(b.potential_score) || 0));
    else result.sort((a, b) => (Number(b.potential_score) || 0) - (Number(a.potential_score) || 0));

    if (trendingOnly) result = result.filter(s => (s.potential_score || 0) > 80);

    setFilteredSellers(result);
    setCurrentPage(1);
  }, [searchQuery, sortBy, sellers, categoryFilter, cityFilter, provinceFilter, trendingOnly, platformFilter]);

  const handleScrape = async () => {
    if (!searchQuery.trim()) { toast.error('Ketik username dahulu!'); return; }
    const clean = searchQuery.trim().replace('@', '');
    try {
      await supabase.from('search_queries').upsert({ query: clean, status: 'pending' }, { onConflict: 'query' });
      toast.success(`Tugas @${clean} dikirim`);
      setIsProcessing(true); setActiveScraping(clean);
    } catch (err) { toast.error('Gagal kirim tugas'); }
  };

  const handleLogout = () => { onLogout(); };

  const handleDownloadExcel = () => {
    if (filteredSellers.length === 0) { toast.error('Data kosong'); return; }
    const dataToExport = filteredSellers.map(s => ({ 'Username': s.username, 'Display Name': s.display_name, 'Followers': s.followers_count, 'No HP': s.phone_number, 'Kategori': s.category, 'Provinsi': s.province, 'Kota': s.city, 'Potensi Skor': s.potential_score, 'Analisis AI': s.potential_reason }));
    const ws = XLSX.utils.json_to_sheet(dataToExport);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Sellers");
    XLSX.writeFile(wb, `Data_Seller_${new Date().toISOString().split('T')[0]}.xlsx`);
    toast.success('Excel didownload');
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
            </div>
            <div className="hidden md:flex bg-white/5 p-1 rounded-2xl border border-white/5 ml-4">
              <button onClick={() => setActiveTab('data')} className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'data' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Dashboard</button>
              {user?.role === 'admin' && (
                <button onClick={() => setActiveTab('users')} className={`px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all ${activeTab === 'users' ? 'bg-indigo-600 text-white' : 'text-slate-500'}`}>Admin</button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
             <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-white/5 rounded-xl">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></div>
              <span className="text-[10px] font-black uppercase text-slate-400">Database Connected</span>
            </div>
            <button onClick={handleLogout} className="p-2.5 text-slate-500 hover:text-rose-500 transition-colors"><LogOut className="w-5 h-5" /></button>
          </div>
        </div>
      </nav>

      <main className="max-w-[1600px] mx-auto px-6 pt-12 pb-12">
        {activeTab === 'users' ? <UserManagement /> : (
          <div className="space-y-12">
            <div className="text-center space-y-6 mb-16">
              <div className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-full text-[10px] font-black uppercase text-indigo-400 tracking-widest">🎯 Indonesia SME Discovery Database</div>
              <h1 className="text-6xl lg:text-7xl font-black uppercase leading-[0.85] text-white">Database UMKM<br /><span className="text-indigo-500">Hasil Analisis AI</span></h1>

              <div className="flex flex-wrap justify-center gap-4 mt-12">
                <StatCard value={stats.total} label="TOTAL DATA SELLERS" />
                <button onClick={fetchSellers} className="p-6 bg-indigo-600/10 text-indigo-400 rounded-[2rem] border border-indigo-500/20 hover:bg-indigo-600 hover:text-white transition-all shadow-xl group">
                    <RefreshCw className={`w-10 h-10 ${loading ? 'animate-spin' : 'group-hover:rotate-180 transition-transform duration-500'}`} />
                </button>
                <StatCard value={stats.provinces} label="PROVINSI TERCOVER" />
                <StatCard value={stats.cities} label="KOTA TERDATA" />
              </div>
            </div>

            <div className="bg-[#12141d]/80 backdrop-blur-xl border border-white/5 rounded-[3rem] p-10 shadow-2xl">
                <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                    <div className="space-y-2">
                        <label className="text-[10px] font-black text-slate-500 uppercase ml-2">Cari Nama/Username</label>
                        <div className="relative">
                            <input className="w-full bg-[#161922] border border-white/5 rounded-2xl pl-12 pr-4 py-4 text-xs font-bold outline-none text-white focus:ring-2 focus:ring-indigo-500" placeholder="Ketik nama..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
                            <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                        </div>
                    </div>
                    <div className="space-y-2">
                        <label className="text-[10px] font-black text-slate-500 uppercase ml-2">Filter Provinsi</label>
                        <select className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-xs focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold appearance-none" value={provinceFilter} onChange={e => { setProvinceFilter(e.target.value); setCityFilter('all'); }}>
                            <option value="all">-- Semua Provinsi --</option>
                            {availableProvinces.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-[10px] font-black text-slate-500 uppercase ml-2">Filter Kategori</label>
                        <select className="w-full bg-[#161922] border border-white/5 rounded-2xl px-6 py-4 text-xs focus:ring-2 focus:ring-indigo-500 outline-none text-white font-bold appearance-none" value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)}>
                            <option value="all">-- Semua Kategori --</option>
                            {CATEGORIES.map(cat => <option key={cat} value={cat}>{cat}</option>)}
                        </select>
                    </div>
                    <div className="flex items-end gap-2">
                        <button onClick={() => setTrendingOnly(!trendingOnly)} className={`flex-1 py-4 rounded-2xl text-[10px] font-black uppercase transition-all ${trendingOnly ? 'bg-rose-600 text-white' : 'bg-white/5 text-slate-500'}`}>🔥 Trending</button>
                        <button onClick={handleDownloadExcel} className="p-4 bg-emerald-600 text-white rounded-2xl"><TrendingUp className="w-4 h-4" /></button>
                    </div>
                </div>
            </div>

            <div className="animate-fade-in">
                <SellerTable sellers={filteredSellers.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage)} loading={loading} />

                {!loading && filteredSellers.length > itemsPerPage && (
                  <div className="mt-12 flex items-center justify-center gap-6">
                    <button onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))} disabled={currentPage === 1} className="px-8 py-4 bg-white/5 border border-white/5 rounded-2xl font-black text-[10px] uppercase hover:bg-white/10 disabled:opacity-30">Sebelumnya</button>
                    <div className="text-xl font-black text-indigo-400 italic">{currentPage} <span className="text-xs text-slate-600">/ {Math.ceil(filteredSellers.length / itemsPerPage)}</span></div>
                    <button onClick={() => setCurrentPage(prev => Math.min(prev + 1, Math.ceil(filteredSellers.length / itemsPerPage)))} disabled={currentPage === Math.ceil(filteredSellers.length / itemsPerPage)} className="px-8 py-4 bg-white/5 border border-white/5 rounded-2xl font-black text-[10px] uppercase hover:bg-white/10 disabled:opacity-30">Selanjutnya</button>
                  </div>
                )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function StatCard({ value, label }) {
  return (
    <div className="bg-[#12141d] border border-white/5 rounded-[2.5rem] p-10 min-w-[280px] shadow-2xl hover:border-indigo-500/30 transition-all text-center">
      <div className="text-5xl font-black text-white italic tracking-tighter mb-2">{value?.toLocaleString() || 0}</div>
      <div className="text-[10px] font-black text-slate-500 uppercase tracking-[0.3em]">{label}</div>
    </div>
  );
}

export default Dashboard;
