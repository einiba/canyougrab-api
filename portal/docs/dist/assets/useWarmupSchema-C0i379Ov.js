import{Q as u,R as n,o as t,W as s}from"./entry.client-hi4ZRsv0.js";const r=s(`
  query SchemaWarmup($input: JSON!, $type: SchemaType!) {
    schema(input: $input, type: $type) {
      openapi
    }
  }
`),y=()=>{const{input:e,type:a}=u(),p=n(r,{input:e,type:a});t({...p,enabled:typeof window<"u",notifyOnChangeProps:[]})};export{y as u};
//# sourceMappingURL=useWarmupSchema-C0i379Ov.js.map
