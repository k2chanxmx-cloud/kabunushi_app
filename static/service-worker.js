const CACHE_NAME = "yutai-v3";

self.addEventListener("install", event => {

    event.waitUntil(

        caches.open(CACHE_NAME)
            .then(cache => {

                return cache.addAll([
                    "/"
                ]);

            })

    );

});


self.addEventListener("fetch", event => {

    event.respondWith(

        caches.match(event.request)
            .then(response => {

                return response || fetch(event.request);

            })

    );

});


self.addEventListener("push", event => {

    let data = {
        title: "株主優待管理",
        body: "通知があります"
    };

    if (event.data) {

        data = event.data.json();

    }

    event.waitUntil(

        self.registration.showNotification(
            data.title,
            {
                body: data.body,
                icon: "/static/icons/icon-192.png",
                badge: "/static/icons/icon-192.png",
                vibrate: [200, 100, 200]
            }
        )

    );

});


self.addEventListener("notificationclick", event => {

    event.notification.close();

    event.waitUntil(

        clients.openWindow("/")

    );

});