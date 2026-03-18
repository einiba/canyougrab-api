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

// Track identification state
var _cwIdentified = false;

function _cwGetAuth0User() {
  try {
    var keys = Object.keys(localStorage);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf("@@auth0spajs@@") !== -1) {
        var data = JSON.parse(localStorage.getItem(keys[i]));
        var user =
          data &&
          data.body &&
          data.body.decodedToken &&
          data.body.decodedToken.user;
        if (user && user.email) return user;
      }
    }
  } catch (e) {}
  return null;
}

function _cwIdentifyUser() {
  if (_cwIdentified || !window.$chatwoot) return false;
  var user = _cwGetAuth0User();
  if (!user) return false;
  window.$chatwoot.setUser(user.sub, {
    email: user.email,
    name: user.name || user.nickname,
    avatar_url: user.picture,
  });
  _cwIdentified = true;
  return true;
}

window.addEventListener("chatwoot:ready", function () {
  window.$chatwoot.setColorScheme("dark");

  // Try to identify immediately; if Auth0 hasn't loaded yet, retry
  if (!_cwIdentifyUser()) {
    var attempts = 0;
    var interval = setInterval(function () {
      attempts++;
      if (_cwIdentifyUser() || attempts > 20) {
        clearInterval(interval);
      }
    }, 500);
  }
});
