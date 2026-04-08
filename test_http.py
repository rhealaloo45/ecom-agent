import urllib.request, urllib.error
class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not redirect
opener = urllib.request.build_opener(NoRedirectHandler())
try:
    res = opener.open('http://localhost:5001', timeout=5)
    print(res.getcode())
    print("Success")
except Exception as e:
    print(e)


