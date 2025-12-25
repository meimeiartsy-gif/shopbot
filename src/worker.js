export default {
  async fetch(request, env, ctx) {
    return new Response(
      "Shop Mini App is running",
      { headers: { "content-type": "text/plain" } }
    );
  }
};
