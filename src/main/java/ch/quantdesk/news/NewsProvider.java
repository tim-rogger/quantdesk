package ch.quantdesk.news;

import java.util.List;

public interface NewsProvider {

    List<NewsItem> latest(String symbol);
}
