import{c as l,Q as k,R as C,U as S,d as e,H as w,B as b,ae as I,bs as _,a0 as d,bj as h,a1 as u,a2 as g,bt as j,a3 as $,Y as z,Z as M,bu as q,ao as f,W as D}from"./entry.client-CWqTFTxJ.js";import{Markdown as v}from"./Markdown-Btuin8C_.js";import{P as O,A as P}from"./ApiHeader-DL5JdiLy.js";import{P as T,c as L,b as A}from"./Popover-BB1f-5zJ.js";import{s as y}from"./slugify-Cgpt3tma.js";import{u as B}from"./useWarmupSchema-DTDtAvIQ.js";import"./useHighlighter-BW452rYv.js";import"./shiki-CMMOmnrO.js";import"./index-DaNioPRk.js";import"./ClaudeLogo-wIIGC-XH.js";import"./download-DeTSSbQW.js";const H=[["path",{d:"M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1",key:"ezmyqa"}],["path",{d:"M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1",key:"e1hn23"}]],Q=l("braces",H);const W=[["circle",{cx:"12",cy:"12",r:"10",key:"1mglay"}],["path",{d:"M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20",key:"13o1zl"}],["path",{d:"M2 12h20",key:"9i4pu4"}]],R=l("globe",W);const E=[["path",{d:"m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7",key:"132q7q"}],["rect",{x:"2",y:"4",width:"20",height:"16",rx:"2",key:"izxlao"}]],G=l("mail",E);const J=[["path",{d:"M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2",key:"q3hayz"}],["path",{d:"m6 17 3.13-5.78c.53-.97.1-2.18-.5-3.1a4 4 0 1 1 6.89-4.06",key:"1go1hn"}],["path",{d:"m12 6 3.13 5.73C15.66 12.7 16.9 13 18 13a4 4 0 0 1 0 8",key:"qlwsc0"}]],U=l("webhook",J),V=D(`
  query SchemaInfo($input: JSON!, $type: SchemaType!) {
    schema(input: $input, type: $type) {
      servers {
        url
        description
      }
      license {
        name
        url
        identifier
      }
      termsOfService
      externalDocs {
        description
        url
      }
      contact {
        name
        url
        email
      }
      description
      summary
      title
      url
      version
      tags {
        name
        description
      }
      components {
        schemas {
          name
        }
      }
      webhooks {
        name
        method
        summary
        description
      }
    }
  }
`),c=({href:s,icon:r,children:i})=>e.jsxs("a",{href:s,className:"inline-flex items-center gap-2 opacity-65 hover:opacity-100 [&_svg]:shrink-0 [&_svg]:size-3.5",target:"_blank",rel:"noopener noreferrer",children:[r,e.jsx("span",{className:"truncate grow-0",children:i})]}),N=({schema:s})=>{const r=!!(s.license||s.termsOfService||s.externalDocs),i=!!(s.contact?.name||s.contact?.email||s.contact?.url),t=s.servers.length>0;return e.jsxs(q,{className:"flex flex-col gap-3 text-sm",children:[r&&e.jsxs("div",{className:"flex flex-col gap-1.5",children:[s.license&&e.jsx(c,{href:s.license.url??void 0,children:s.license.name}),s.termsOfService&&e.jsx(c,{href:s.termsOfService,children:"Terms of Service"}),s.externalDocs&&e.jsx(c,{href:s.externalDocs.url,children:s.externalDocs.description??"Documentation"})]}),r&&(i||t)&&e.jsx(f,{}),i&&e.jsxs("div",{className:"flex flex-col gap-1.5",children:[e.jsx("span",{className:"text-xs text-muted-foreground font-medium uppercase tracking-wide",children:"Contact"}),s.contact?.name&&e.jsx("span",{children:s.contact.name}),s.contact?.email&&e.jsx(c,{href:`mailto:${s.contact.email}`,icon:e.jsx(G,{}),children:s.contact.email}),s.contact?.url&&e.jsx(c,{href:s.contact.url,icon:e.jsx(R,{}),children:s.contact.url})]}),i&&t&&e.jsx(f,{}),t&&e.jsxs("div",{className:"flex flex-col gap-1.5",children:[e.jsx("span",{className:"text-xs text-muted-foreground font-medium uppercase tracking-wide",children:"Servers"}),s.servers.map(n=>e.jsxs("div",{children:[e.jsx("code",{className:"text-xs select-all break-all",children:n.url}),n.description&&e.jsx("p",{className:"text-muted-foreground text-xs",children:n.description})]},n.url))]})]})},re=()=>{const{input:s,type:r}=k(),i=C(V,{input:s,type:r}),{data:{schema:t}}=S(i),{title:n,description:m}=t;B();const x=!!(t.contact?.name||t.contact?.email||t.contact?.url||t.servers.length>0||t.license||t.termsOfService||t.externalDocs),p=t.tags.flatMap(({name:a,description:o})=>a?{name:a,description:o}:[]);return e.jsxs("div",{className:"pt-(--padding-content-top)","data-pagefind-filter":"section:openapi","data-pagefind-meta":"section:openapi",children:[e.jsx(O,{name:"category",children:n}),e.jsxs(w,{children:[n&&e.jsx("title",{children:n}),m&&e.jsx("meta",{name:"description",content:m})]}),e.jsxs("div",{className:"mb-8 flex flex-col gap-4",children:[e.jsx(P,{heading:n,headingId:"description"}),e.jsxs("div",{className:"grid grid-cols-1 xl:grid-cols-[1fr_minmax(250px,380px)] gap-8",children:[x&&e.jsx("div",{className:"xl:hidden sticky top-(--top-nav-height) lg:top-(--scroll-padding) z-10 row-start-1 col-start-1 justify-self-end self-start",children:e.jsxs(T,{children:[e.jsx(L,{asChild:!0,children:e.jsx(b,{variant:"outline",size:"icon",className:"shadow-sm rounded-full",children:e.jsx(I,{})})}),e.jsx(A,{align:"end",className:"xl:hidden w-full max-w-full md:max-w-sm",children:e.jsx(N,{schema:t})})]})}),e.jsxs("div",{className:"flex flex-col gap-6 row-start-1 col-start-1",children:[t.summary&&e.jsx("p",{className:"text-lg text-muted-foreground",children:t.summary}),t.description&&e.jsx(v,{className:"prose-img:max-w-prose prose-sm max-w-full lg:max-w-2xl",content:t.description}),p.length>0&&e.jsxs("div",{children:[e.jsxs("div",{className:"flex items-center gap-2 text-sm uppercase tracking-wide text-muted-foreground mb-4",children:[e.jsx(_,{size:14}),"Tags"]}),e.jsx("div",{className:"grid grid-cols-1 md:grid-cols-2 gap-4",children:p.map(a=>e.jsx(d,{variant:"outline",asChild:!0,children:e.jsx(h,{to:y(a.name),children:e.jsxs(u,{children:[e.jsx(g,{children:a.name}),a.description&&e.jsx(j,{asChild:!0,children:e.jsx(v,{components:{p:({children:o})=>o},content:a.description,className:"prose-sm text-pretty"})})]})})},a.name))})]}),(t.components?.schemas?.length??0)>0&&e.jsxs("div",{children:[e.jsxs("div",{className:"flex items-center gap-2 text-sm uppercase tracking-wide text-muted-foreground mb-4",children:[e.jsx(Q,{size:14}),"Schemas"]}),e.jsx("div",{className:"grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4",children:t.components?.schemas?.map(a=>e.jsx(d,{variant:"outline",title:a.name,asChild:!0,children:e.jsx(h,{to:`~schemas#${y(a.name)}`,children:e.jsx("span",{className:"text-sm font-medium leading-snug truncate",children:a.name})})},a.name))})]}),t.webhooks.length>0&&e.jsxs("div",{children:[e.jsxs("div",{className:"flex items-center gap-2 text-sm uppercase tracking-wide text-muted-foreground mb-4",children:[e.jsx(U,{size:14}),"Webhooks"]}),e.jsx("div",{className:"grid grid-cols-1 md:grid-cols-2 gap-4",children:t.webhooks.map(a=>e.jsxs(d,{variant:"outline",children:[e.jsxs(u,{children:[e.jsx(g,{children:a.name}),(a.summary||a.description)&&e.jsx(j,{children:a.summary??a.description})]}),e.jsx($,{children:e.jsx(z,{variant:"muted",className:"text-[10px] font-mono",children:a.method})})]},`${a.name}-${a.method}`))})]})]}),x&&e.jsx("div",{className:"hidden xl:block",children:e.jsx(M,{className:"sticky top-(--scroll-padding)",children:e.jsx(N,{schema:t})})})]})]})]})};export{re as SchemaInfo};
//# sourceMappingURL=SchemaInfo-jnxrUziW.js.map
