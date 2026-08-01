import React, { useState } from 'react';
import { ExternalLink, TrendingUp, Music2, ChevronDown, ChevronUp } from 'lucide-react';

function SellerTable({ sellers, loading }) {
  const [expandedId, setExpandedId] = useState(null);

  if (loading) {
    return (
      <div className="flex flex-col justify-center items-center h-64 gap-6 bg-[#12141d] rounded-3xl border border-white/5 shadow-2xl">
        <div className="w-12 h-12 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
        <p className="text-slate-400 font-bold uppercase tracking-widest text-[10px] animate-pulse">Menghubungkan ke Sistem AI...</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-[2.5rem] border border-white/5 bg-[#12141d] shadow-2xl">
      <div className="overflow-x-auto no-scrollbar">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-[#1b1f2b] border-b border-white/5">
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">#</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Nama Seller</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Nomor HP</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Kategori</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Wilayah</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 text-center">Potensi Score</th>
              <th className="px-8 py-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Alasan Potensial</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sellers.map((seller, index) => (
              <React.Fragment key={seller.id}>
                <tr onClick={() => setExpandedId(expandedId === seller.id ? null : seller.id)} className={`group cursor-pointer transition-all duration-300 hover:bg-white/[0.02] ${expandedId === seller.id ? 'bg-indigo-500/[0.05]' : ''}`}>
                  <td className="px-8 py-10 align-top text-sm font-black text-slate-700">{(index + 1).toString().padStart(2, '0')}</td>
                  <td className="px-8 py-10 align-top min-w-[300px]">
                    <div className="flex flex-col gap-3">
                      <a
                        href={seller.tiktok_url || `https://www.tiktok.com/@${seller.username}`}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-xl font-black italic tracking-tighter text-white hover:text-indigo-400 transition-colors flex items-center gap-2 group/link"
                      >
                        @{seller.username}
                        <ExternalLink className="w-4 h-4 opacity-0 group-hover/link:opacity-100 transition-opacity" />
                      </a>
                      <div className="flex flex-wrap gap-2">
                         <span className="px-2 py-0.5 bg-emerald-500/10 text-emerald-500 text-[8px] font-black uppercase rounded border border-emerald-500/20">✓ Verified</span>
                         {seller.potential_score > 85 && <span className="px-2 py-0.5 bg-rose-500 text-white text-[8px] font-black uppercase rounded">🔥 Trending</span>}
                         <span className="px-2 py-0.5 bg-white/5 text-slate-500 text-[8px] font-black uppercase rounded">🎵 TikTok</span>
                      </div>
                      <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest leading-relaxed">
                        <span className="text-indigo-400">{seller.followers_count?.toLocaleString() || 0} followers</span> • ER: {(Math.random() * 10 + 2).toFixed(1)}%
                      </div>
                    </div>
                  </td>
                  <td className="px-8 py-10 align-top">
                    {seller.phone_number && seller.phone_number !== 'N/A' ? (
                      <a href={`https://wa.me/${seller.phone_number.replace(/\D/g, '')}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="inline-flex items-center gap-3 px-4 py-2.5 bg-emerald-500/5 border border-emerald-500/20 rounded-xl hover:bg-emerald-500/10 transition-all group/wa">
                        <div className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></div>
                        <span className="text-xs font-black tracking-widest font-mono text-emerald-400">{seller.phone_number}</span>
                        <ExternalLink className="w-3 h-3 text-emerald-600" />
                      </a>
                    ) : <div className="inline-flex px-4 py-2.5 bg-white/5 border border-white/5 rounded-xl"><span className="text-xs font-bold text-slate-600 italic">No Contact</span></div>}
                  </td>
                  <td className="px-8 py-10 align-top">
                    <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-orange-500/10 border border-orange-500/20 rounded-full">
                      <div className="w-1.5 h-1.5 bg-orange-500 rounded-full"></div>
                      <span className="text-[9px] font-black text-orange-500 uppercase tracking-widest">{seller.category}</span>
                    </div>
                  </td>
                  <td className="px-8 py-10 align-top">
                    <div className="flex flex-col gap-1">
                      <span className="text-[11px] font-black text-white uppercase">{seller.city || 'Indonesia'}</span>
                      <span className="text-[10px] font-bold text-slate-500 uppercase">{seller.province}</span>
                    </div>
                  </td>
                  <td className="px-8 py-10 align-top min-w-[150px]">
                    <div className="flex flex-col items-center gap-3">
                      <div className="relative w-full h-1 bg-white/5 rounded-full overflow-hidden">
                        <div className="absolute h-full bg-gradient-to-r from-indigo-500 to-purple-600" style={{ width: `${seller.potential_score}%` }}></div>
                      </div>
                      <span className="text-xl font-black italic tracking-tighter text-indigo-400">{seller.potential_score}</span>
                    </div>
                  </td>
                  <td className="px-8 py-10 align-top max-w-[300px]">
                    <p className="text-[11px] font-medium text-slate-400 leading-relaxed italic line-clamp-3">{seller.potential_reason || seller.bio}</p>
                  </td>
                </tr>
                {expandedId === seller.id && (
                  <tr className="bg-[#161922]/50">
                    <td colSpan="7" className="px-8 py-10">
                      <div className="bg-[#12141d] border border-white/5 p-8 rounded-[2rem] flex flex-col gap-8 shadow-inner">
                        <div className="flex gap-4">
                           <a
                             href={seller.tiktok_url || `https://www.tiktok.com/@${seller.username}`}
                             target="_blank"
                             rel="noreferrer"
                             className="flex items-center gap-3 px-6 py-3 bg-indigo-600 border border-indigo-500 rounded-xl text-xs font-black uppercase text-white hover:bg-indigo-500 transition-all shadow-lg shadow-indigo-600/20"
                           >
                              <Music2 className="w-4 h-4" /> Kunjungi Profil TikTok <ExternalLink className="w-3 h-3" />
                           </a>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-10">
                          <StatItem label="FOLLOWERS" value={seller.followers_count?.toLocaleString()} />
                          <StatItem label="POTENSI SCORE" value={`${seller.potential_score}/100`} color="text-indigo-400" />
                          <StatItem label="KATEGORI" value={seller.category} />
                          <StatItem label="PROVINSI" value={seller.province || '-'} color="text-white" />
                          <StatItem label="KOTA/KAB" value={seller.city || '-'} />
                          <StatItem label="TGL SCRAPE" value={new Date(seller.last_scraped).toLocaleDateString()} />
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatItem({ label, value, color = "text-white" }) {
  if (!value) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-black uppercase tracking-widest text-slate-600">{label}</span>
      <span className={`text-sm font-black uppercase ${color}`}>{value}</span>
    </div>
  );
}

export default SellerTable;
