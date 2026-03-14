const r=e=>e.normalize("NFKD").replace(/\p{Diacritic}/gu,"").toLowerCase().replace(/['\u2018\u2019\u201C\u201D]/g,"").replace(/[^\p{L}\p{N}]+/gu,"-").replace(/^-+|-+$/g,""),u=()=>{const e=new Map;return c=>{const t=r(c),s=e.get(t)??0;return e.set(t,s+1),s===0?t:`${t}-${s+1}`}};export{u as a,r as s};
//# sourceMappingURL=slugify-Cgpt3tma.js.map
