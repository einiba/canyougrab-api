import{Q as g,R as x,U as j,d as e,H as c,ag as f,e as y,ah as S,B as b,bl as N,ai as C,W as v}from"./entry.client-D1m5fYl7.js";import{T as w}from"./Toc-DyMFz8dW.js";import{P as A,A as H}from"./ApiHeader-CmpcfxWT.js";import{s as m}from"./slugify-Cgpt3tma.js";import{S as T}from"./SchemaView-d7MC4FZN.js";import"./ClaudeLogo-DGZeMfmi.js";import"./download-F-9ZLysg.js";import"./Markdown-wVnEIm8o.js";import"./useHighlighter-s-vWoE5e.js";import"./shiki-BCZcig9Z.js";import"./index-qElI546I.js";import"./constants-B942wETt.js";const V=v(`
  query GetSchemas($input: JSON!, $type: SchemaType!) {
    schema(input: $input, type: $type) {
      title
      description
      summary
      components {
        schemas {
          name
          schema
          extensions
        }
      }
    }
  }
`);function G(){const{input:l,type:p,versions:h,version:a,options:i}=g(),d=x(V,{input:l,type:p}),{data:n}=j(d),o=n.schema.title,t=n.schema.components?.schemas??[],u=Object.entries(h).length>1,r=i?.showVersionSelect==="always"||u&&i?.showVersionSelect!=="hide";return t.length?e.jsxs("div",{className:"grid grid-cols-(--sidecar-grid-cols) gap-8 justify-between","data-pagefind-filter":"section:openapi","data-pagefind-meta":"section:openapi",children:[e.jsx(A,{name:"category",children:o}),e.jsxs(c,{children:[e.jsxs("title",{children:["Schemas ",r?a:""]}),e.jsx("meta",{name:"description",content:"List of schemas used by the API."})]}),e.jsxs("div",{className:"pt-(--padding-content-top) pb-(--padding-content-bottom)",children:[e.jsx(H,{title:o,heading:"Schemas",headingId:"schemas"}),e.jsx("hr",{className:"my-8"}),e.jsx("div",{className:"flex flex-col gap-y-5",children:t.map(s=>e.jsxs(f,{className:"group",defaultOpen:!0,children:[e.jsxs(y,{registerNavigationAnchor:!0,level:2,className:"flex items-center gap-1 justify-between w-fit",id:m(s.name),children:[s.name," ",e.jsx(S,{asChild:!0,children:e.jsx(b,{variant:"ghost",size:"icon",className:"size-6",children:e.jsx(N,{size:16,className:"group-data-[state=open]:rotate-90 transition cursor-pointer"})})})]}),e.jsx(C,{className:"mt-4 CollapsibleContent",children:e.jsx(T,{schema:s.schema})})]},s.name))})]}),e.jsx(w,{entries:t.map(s=>({id:m(s.name),text:s.name,depth:1}))})]}):e.jsxs("div",{children:[e.jsxs(c,{children:[e.jsxs("title",{children:["Schemas ",r?a:""]}),e.jsx("meta",{name:"description",content:"List of schemas used by the API."})]}),"No schemas found"]})}export{G as SchemaList};
//# sourceMappingURL=SchemaList-krOHMDUW.js.map
