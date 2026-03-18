(function (d, t) {
  var hostname = window.location.hostname;
  var isDev =
    hostname.includes("dev") ||
    hostname === "localhost" ||
    hostname === "127.0.0.1";
  var BASE_URL = "https://chatwoot.canyougrab.it";
  var websiteToken = isDev
    ? "99as6UKZNBJkBknq76TZoZcx"
    : "hXtVKFiB5VSRJJFQMnLakXo3";
  var g = d.createElement(t),
    s = d.getElementsByTagName(t)[0];
  g.src = BASE_URL + "/packs/js/sdk.js";
  g.defer = true;
  g.async = true;
  s.parentNode.insertBefore(g, s);
  g.onload = function () {
    window.chatwootSDK.run({
      websiteToken: websiteToken,
      baseUrl: BASE_URL,
    });
  };
})(document, "script");

window.addEventListener("chatwoot:ready", function () {
  window.$chatwoot.setColorScheme("dark");
});
