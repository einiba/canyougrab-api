import{Q as u,R as n,o as t,W as s}from"./entry.client-ngYrHrOO.js";const r=s(`
  query SchemaWarmup($input: JSON!, $type: SchemaType!) {
    schema(input: $input, type: $type) {
      openapi
    }
  }
`),y=()=>{const{input:e,type:a}=u(),p=n(r,{input:e,type:a});t({...p,enabled:typeof window<"u",notifyOnChangeProps:[]})};export{y as u};
//# sourceMappingURL=useWarmupSchema-BYWWv9Kc.js.map
